"""Lambda entry point: the HTTP API (API Gateway HTTP API, payload v2) and the analysis queue (SQS)."""

import json
import os
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response, content_types
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, StringConstraints, ValidationError

from .analysis.assets import Assets, load_assets
from .analysis.costs import to_micro_usd
from .analysis.llm import bedrock_client
from .checks import api as checks_api
from .checks.store import ChecksStore, Limits
from .checks.worker import WorkerDeps, process
from .embeddings import Embedder
from .search import Hit, SearchIndex, is_verifiable, load_index, search

logger = Logger()
app = APIGatewayHttpResolver()

# scripts/build_lambda.py packages the index, the prompts and the analysis
# configuration next to this module.
DATA_DIR = Path(__file__).parent / "data"
INDEX_PATH = Path(os.environ.get("SEARCH_INDEX_PATH", str(DATA_DIR / "index.json")))
QUERY_LOG_CHARS = 200


def _load_search_index() -> SearchIndex | None:
    """Loads the index once per cold start. A missing or broken index disables /search, not /health."""
    try:
        return load_index(INDEX_PATH)
    except FileNotFoundError:
        logger.warning("Search index not packaged", extra={"path": str(INDEX_PATH)})
    except (ValueError, KeyError, TypeError) as error:
        logger.exception("Search index is unreadable", extra={"path": str(INDEX_PATH), "error": str(error)})
    return None


search_index = _load_search_index()
_embedder: Embedder | None = None


def get_embedder(index: SearchIndex) -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder(index.model, index.dimensions)
    return _embedder


def _load_analysis() -> tuple[Assets | None, str | None]:
    """The prompts, configuration and analysis_version the build packaged; none means no analyses."""
    try:
        assets = load_assets(DATA_DIR)
        version = (DATA_DIR / "analysis_version.txt").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("Analysis not packaged", extra={"path": str(DATA_DIR)})
        return None, None
    return assets, version


analysis_assets, analysis_version = _load_analysis()
_clients: dict[str, Any] = {}
_worker_embedder: Embedder | None = None


def _client(name: str) -> Any:
    if name not in _clients:
        import boto3

        _clients[name] = bedrock_client(for_ci=False) if name == "bedrock-runtime" else boto3.client(name)
    return _clients[name]


def _log(message: str, **fields: Any) -> None:
    logger.info(message, extra=fields)


def analysis_unavailable_reason() -> str | None:
    if analysis_assets is None or not analysis_version:
        return "the analysis is not packaged"
    reason = _unavailable_reason(search_index)
    if reason:
        return reason
    if analysis_assets.generation_model != os.environ.get("GENERATION_MODEL_ID"):
        return "the packaged prompts name another generation model"
    if not os.environ.get("CHECKS_TABLE") or not os.environ.get("CHECKS_QUEUE_URL"):
        return "the checks table or queue is not configured"
    return None


def checks_store() -> ChecksStore:
    return ChecksStore(_client("dynamodb"), os.environ.get("CHECKS_TABLE", ""))


def per_check_cap_micro() -> int:
    from decimal import Decimal

    return to_micro_usd(Decimal(str(analysis_assets.analysis["limits"]["per_check_cap_usd"]))) if analysis_assets else 0


def checks_deps() -> checks_api.CheckDeps:
    from decimal import Decimal

    reason = analysis_unavailable_reason()
    limits_config = analysis_assets.analysis["limits"] if analysis_assets else {"jd_min_chars": 200, "jd_max_chars": 20000}
    return checks_api.CheckDeps(
        store=checks_store(),
        enqueue=checks_api.queue_sender(_client("sqs"), os.environ.get("CHECKS_QUEUE_URL", "")),
        limits=Limits(
            daily_per_ip=int(os.environ.get("DAILY_CHECKS_PER_IP", "5")),
            monthly_cap_micro=to_micro_usd(Decimal(os.environ.get("PUBLIC_MONTHLY_CAP_USD", "0"))),
            per_check_cap_micro=per_check_cap_micro(),
        ),
        jd_min_chars=int(limits_config["jd_min_chars"]),
        jd_max_chars=int(limits_config["jd_max_chars"]),
        versions=None
        if reason
        else {"analysis_version": analysis_version, "model": analysis_assets.generation_model, "index_version": search_index.index_version},
        unavailable=reason,
        clock=time.time,
        log=_log,
    )


def worker_deps() -> WorkerDeps:
    global _worker_embedder
    reason = analysis_unavailable_reason()
    if _worker_embedder is None and search_index is not None:
        # Titan calls are cheap and idempotent: adaptive retries ride out a burst.
        _worker_embedder = Embedder(search_index.model, search_index.dimensions, max_attempts=5, retry_mode="adaptive")
    return WorkerDeps(
        store=checks_store(),
        assets=analysis_assets,
        index=search_index,
        claude=None if reason else _client("bedrock-runtime"),
        embedder=_worker_embedder,
        per_check_cap_micro=per_check_cap_micro(),
        clock=time.time,
        log=_log,
        unavailable=reason,
    )


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain a non-space character")
    return value


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, StringConstraints(min_length=1, max_length=500), AfterValidator(_not_blank)]
    k: StrictInt = Field(default=5, ge=1, le=10)
    # The requirement's category, when known, routes the search to the card
    # kinds that can answer it (fitcheck_api.search.allowed_kinds).
    category: Literal["skill", "experience", "education", "language", "location", "domain", "soft"] | None = None


def _json_response(status: int, body: dict) -> Response:
    return Response(status_code=status, content_type=content_types.APPLICATION_JSON, body=json.dumps(body))


def _hit(hit: Hit) -> dict:
    card = hit.card
    return {
        "id": card["id"],
        "kind": card["kind"],
        "title": card["title"],
        "summary": card["summary"],
        "skills": card["skills"],
        "proof": card["proof"],
        "verifiable": is_verifiable(card),
        "score": round(hit.score, 4),
        "similarity": round(hit.similarity, 4),
    }


def _unavailable_reason(index: SearchIndex | None) -> str | None:
    if index is None:
        return "no search index is packaged"
    if index.placeholder:
        return "the packaged search index is a placeholder"
    if index.model != os.environ.get("EMBEDDING_MODEL_ID"):
        return "the search index was built with a different embedding model"
    return None


@app.get("/health")
def health() -> dict:
    # GIT_COMMIT is set per deploy, so each published version reports the
    # commit it was built from.
    commit = os.environ.get("GIT_COMMIT", "unknown")
    logger.info("Health check", extra={"commit": commit})
    return {"ok": True, "commit": commit}


@app.post("/search")
def search_route() -> Response | dict:
    try:
        body = app.current_event.json_body
    except (ValueError, TypeError):
        return _json_response(400, {"error": "the body must be a JSON object"})
    if not isinstance(body, dict):
        return _json_response(400, {"error": "the body must be a JSON object"})
    try:
        request = SearchRequest.model_validate(body)
    except ValidationError as error:
        details = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]} for item in error.errors()]
        return _json_response(400, {"error": "invalid request", "details": details})

    index = search_index
    reason = _unavailable_reason(index)
    if reason is not None:
        logger.error("Search unavailable", extra={"reason": reason})
        return _json_response(503, {"error": "search is unavailable"})

    started = time.perf_counter()
    try:
        vector = get_embedder(index).embed(request.query)
    except (BotoCoreError, ClientError, ValueError):
        # Throttling that outlasted the retries, a denied call or a malformed
        # reply: the caller gets a clean 503, the log keeps the cause.
        logger.exception("Embedding failed")
        return _json_response(503, {"error": "search is unavailable"})
    embedded = time.perf_counter()
    result = search(index, vector, request.query, request.category)
    finished = time.perf_counter()
    gap = result.gaps[0] if result.gaps else None
    logger.info(
        "Search",
        extra={
            "query": request.query[:QUERY_LOG_CHARS],
            "k": request.k,
            "category": request.category,
            "embedding_ms": round((embedded - started) * 1000, 1),
            "search_ms": round((finished - embedded) * 1000, 2),
        },
    )
    # results: positive evidence, ordered by score. gap: the closest gap card,
    # kept out of results so it never takes the place of evidence.
    return {
        "index_version": index.index_version,
        "results": [_hit(hit) for hit in result.evidence[: request.k]],
        "gap": _hit(gap) if gap else None,
    }


@app.post("/checks")
def post_checks_route() -> Response:
    try:
        body = app.current_event.json_body
    except (ValueError, TypeError):
        return _json_response(400, {"error": "the body must be a JSON object"})
    reply = checks_api.post_check(body, app.current_event.request_context.http.source_ip, checks_deps())
    return _json_response(reply.status, reply.body)


@app.get("/checks/<check_id>")
def get_check_route(check_id: str) -> Response:
    if not os.environ.get("CHECKS_TABLE"):
        return _json_response(503, {"error": "analysis is unavailable"})
    reply = checks_api.get_check(check_id, checks_deps())
    return _json_response(reply.status, reply.body)


def _is_queue_event(event: Any) -> bool:
    records = event.get("Records") if isinstance(event, dict) else None
    return bool(records) and records[0].get("eventSource") == "aws:sqs"


def handle_queue(event: dict, context: LambdaContext) -> dict:
    """One message per invocation (batch size 1); an unexpected error fails it for SQS to retry."""
    for record in event["Records"]:
        identifier = json.loads(record["body"])["check_id"]
        logger.set_correlation_id(identifier)
        process(identifier, worker_deps(), context.get_remaining_time_in_millis)
    return {}


# The correlation ID is API Gateway's request ID (also in the apigw-requestid
# response header) for HTTP requests, and the check ID for queue messages.
@logger.inject_lambda_context
def handler(event: dict, context: LambdaContext) -> dict:
    if _is_queue_event(event):
        return handle_queue(event, context)
    logger.set_correlation_id(event.get("requestContext", {}).get("requestId"))
    return app.resolve(event, context)
