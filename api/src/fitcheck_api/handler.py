"""Lambda entry point for the fit-check HTTP API (API Gateway HTTP API, payload v2)."""

import json
import os
import time
from pathlib import Path
from typing import Annotated, Literal

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response, content_types
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, StringConstraints, ValidationError

from .embeddings import Embedder
from .search import Hit, SearchIndex, is_verifiable, load_index, search

logger = Logger()
app = APIGatewayHttpResolver()

# scripts/build_lambda.py packages the index next to this module.
INDEX_PATH = Path(os.environ.get("SEARCH_INDEX_PATH", str(Path(__file__).parent / "data" / "index.json")))
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


# The correlation ID is API Gateway's request ID, which it also returns to the
# caller in the apigw-requestid response header.
@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)
