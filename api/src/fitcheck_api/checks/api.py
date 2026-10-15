"""POST /checks and GET /checks/{id}, as functions of their inputs and injected dependencies."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ..analysis.normalize import normalize_job_description
from .ids import CHECK_ID, check_id, day_key, ip_hash, iso, text_hash
from .store import FINAL, ChecksStore, Limits, is_stale, new_check_item

POLL_AFTER_MS = 3000


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_description: str


@dataclass
class CheckDeps:
    store: ChecksStore
    # Sends {"check_id": ...} to the queue.
    enqueue: Callable[[str], None]
    limits: Limits
    jd_min_chars: int
    jd_max_chars: int
    # analysis_version, model and index_version, or None with the reason analyses are off.
    versions: Mapping[str, str] | None
    unavailable: str | None
    clock: Callable[[], float]
    log: Callable[..., None]


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict[str, Any]


def post_check(body: Any, source_ip: str, deps: CheckDeps) -> Reply:
    if not isinstance(body, dict):
        return Reply(400, {"error": "the body must be a JSON object"})
    try:
        request = CheckRequest.model_validate(body)
    except ValidationError as error:
        details = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]} for item in error.errors()]
        return Reply(400, {"error": "invalid request", "details": details})
    job = normalize_job_description(request.job_description)
    if not deps.jd_min_chars <= len(job) <= deps.jd_max_chars:
        return Reply(
            400,
            {"error": f"job_description must have {deps.jd_min_chars} to {deps.jd_max_chars} characters after normalization, it has {len(job)}"},
        )
    if deps.unavailable or deps.versions is None:
        deps.log("Analysis unavailable", reason=deps.unavailable)
        return Reply(503, {"error": "analysis is unavailable"})

    now = deps.clock()
    identifier = check_id(deps.versions["analysis_version"], job)
    existing = deps.store.get(identifier)
    if existing and existing["status"] != "failed" and not is_stale(existing, now):
        return Reply(200, {"check_id": identifier, "status": existing["status"], "cached": True})

    item = new_check_item(identifier, job, deps.versions, now, iso(now))
    outcome = deps.store.create(item, ip_hash(source_ip), day_key(now), deps.limits, now)
    if outcome.result == "cached":
        return Reply(200, {"check_id": identifier, "status": outcome.status, "cached": True})
    if outcome.result in ("daily_limit", "monthly_budget", "busy"):
        deps.log("Check refused", check_id=identifier, reason=outcome.result)
        return Reply(429, {"error": outcome.result})

    deps.log("Check created", check_id=identifier, job_description_chars=len(job), job_description_sha256=text_hash(job))
    try:
        deps.enqueue(identifier)
    except Exception as error:  # the queue is down or denied: undo, so the reservation does not leak
        deps.log("Enqueue failed", check_id=identifier, error=type(error).__name__)
        now = deps.clock()
        deps.store.fail(identifier, "enqueue_failed", None, now, iso(now))
        deps.store.release_reservation(item["spend_month"], deps.limits.per_check_cap_micro)
        return Reply(503, {"error": "analysis is unavailable"})
    return Reply(202, {"check_id": identifier, "status": "queued", "poll_after_ms": POLL_AFTER_MS})


def check_view(item: Mapping[str, Any], now: float) -> dict[str, Any]:
    """The GET response: requirements as soon as they are extracted, verdicts once judged, never the job description."""
    status, error = item["status"], item.get("error")
    if is_stale(item, now):
        status, error = "failed", "timed_out"
    judgments = {judgment["requirement_id"]: judgment for judgment in item.get("judgments", [])}
    requirements = []
    for requirement in item.get("requirements", []):
        judgment = judgments.get(requirement["id"], {})
        requirements.append(
            {
                "id": requirement["id"],
                "source": requirement["source"],
                "text_en": requirement["text_en"],
                "type": requirement["type"],
                "category": requirement["category"],
                "verdict": judgment.get("verdict"),
                "reason": judgment.get("reason"),
                "downgraded": judgment.get("downgraded"),
                "citations": judgment.get("citations", []),
            }
        )
    return {
        "check_id": item["check_id"],
        "status": status,
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "analysis_version": item["analysis_version"],
        "model": item["model"],
        "index_version": item["index_version"],
        "requirements": requirements,
        "score": item.get("score") if status == "done" else None,
        "usage": {"cost_usd": int(item.get("cost_micro", 0)) / 1_000_000, "latency_ms": item.get("latency_ms")},
        "error": error,
    }


def get_check(identifier: str, deps: CheckDeps) -> Reply:
    if not CHECK_ID.match(identifier):
        return Reply(400, {"error": "invalid check id"})
    item = deps.store.get(identifier)
    if item is None:
        return Reply(404, {"error": "check not found"})
    return Reply(200, check_view(item, deps.clock()))


def is_final(status: str) -> bool:
    return status in FINAL


def queue_sender(sqs_client: Any, queue_url: str) -> Callable[[str], None]:
    def send(identifier: str) -> None:
        sqs_client.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"check_id": identifier}))

    return send
