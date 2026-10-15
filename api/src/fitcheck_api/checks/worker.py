"""The queue side: one message per analysis, run phase by phase so a redelivery resumes.

A lease (a conditional write of lease_until) lets one invocation work on a
check at a time; a duplicate delivery finds it held and returns. Expected
failures (Bedrock errors, an invalid model reply, the cost cap, the deadline)
mark the check failed and return success to the queue. Anything else raises,
so SQS delivers the message again and, after the second failure, moves it to
the dead-letter queue. The spending counters are settled once the check is
final.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..analysis.assets import Assets
from ..analysis.costs import MILLION, AnalysisBudget, CallRecord, to_micro_usd
from ..analysis.errors import DEADLINE, AnalysisError
from ..analysis.extract import Requirement, extract_requirements
from ..analysis.judge import judge_requirements
from ..analysis.llm import READ_TIMEOUT_SECONDS
from ..analysis.retrieve import Candidates, retrieve_candidates
from ..analysis.score import compute_score
from ..embeddings import Embedder
from ..search import SearchIndex
from .ids import iso
from .store import ChecksStore

BEDROCK_ERROR = "bedrock_error"
UNAVAILABLE = "unavailable"
# Before each model call, the invocation must have this much time left.
DEADLINE_MARGIN_SECONDS = READ_TIMEOUT_SECONDS + 15


class DeadlineGuard:
    """A CallGuard that refuses a call the Lambda could not wait for."""

    def __init__(self, remaining_ms: Callable[[], int], margin_seconds: int = DEADLINE_MARGIN_SECONDS) -> None:
        self.remaining_ms = remaining_ms
        self.margin_seconds = margin_seconds

    def before_call(self, phase: str, worst_case_usd: Decimal, cached: bool) -> None:
        remaining = self.remaining_ms()
        if remaining < self.margin_seconds * 1000:
            raise AnalysisError(DEADLINE, f"{phase}: {remaining} ms left, {self.margin_seconds} s needed")

    def after_call(self, record: CallRecord, worst_case_usd: Decimal) -> None:
        pass

    def call_failed(self, worst_case_usd: Decimal, cached: bool) -> None:
        pass


@dataclass
class WorkerDeps:
    store: ChecksStore
    assets: Assets | None
    index: SearchIndex | None
    claude: Any
    embedder: Embedder | None
    per_check_cap_micro: int
    clock: Callable[[], float]
    log: Callable[..., None]
    # Why analyses cannot run in this package, if they cannot.
    unavailable: str | None = None


def _bedrock(call: Callable[[], Any]) -> Any:
    """Bedrock failures are expected: they fail the analysis instead of the message."""
    try:
        return call()
    except (ClientError, BotoCoreError) as error:
        raise AnalysisError(BEDROCK_ERROR, str(error)[:300]) from error


def _citations(judgment: Any, cards: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"card_id": c.card_id, "title": cards[c.card_id]["title"], "quote": c.quote, "proof": cards[c.card_id]["proof"]}
        for c in judgment.citations
    ]


def _log_calls(deps: WorkerDeps, identifier: str, records: list[CallRecord]) -> None:
    for record in records:
        deps.log("Model call", check_id=identifier, **record.log_fields())


def _run(identifier: str, item: dict[str, Any], deps: WorkerDeps, guards: list, budget: AnalysisBudget) -> dict[str, Any]:
    assets, index = deps.assets, deps.index

    def advance(expected: str, status: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        now = deps.clock()
        return deps.store.advance(identifier, expected, status, {**fields, "cost_micro": to_micro_usd(budget.spent_usd)}, now, iso(now))

    wanted = "extracting" if "requirements" not in item else "retrieving" if "candidates" not in item else "judging"
    if item["status"] != wanted:
        item = advance(item["status"], wanted, {})

    if "requirements" not in item:
        extraction = _bedrock(lambda: extract_requirements(item["job_description"], assets, deps.claude, guards))
        _log_calls(deps, identifier, extraction.records)
        item = advance(
            "extracting",
            "retrieving",
            {"requirements": [r.to_dict() for r in extraction.requirements], "dropped_unsourced": extraction.dropped_unsourced},
        )

    if "candidates" not in item:
        requirements = [Requirement(**r) for r in item["requirements"]]
        candidates, records = _bedrock(
            lambda: retrieve_candidates(
                requirements,
                index,
                deps.embedder,
                int(assets.analysis["judge"]["evidence_k"]),
                assets.price(assets.embedding_model),
                guards,
            )
        )
        _log_calls(deps, identifier, records)
        item = advance(
            "retrieving",
            "judging",
            {"candidates": [{"requirement_id": c.requirement_id, "evidence": list(c.evidence), "gap": c.gap} for c in candidates]},
        )

    requirements = [Requirement(**r) for r in item["requirements"]]
    candidates = [Candidates(c["requirement_id"], tuple(c["evidence"]), c.get("gap")) for c in item["candidates"]]
    cards = {card["id"]: card for card in index.cards}
    judgment = _bedrock(lambda: judge_requirements(requirements, candidates, cards, assets, deps.claude, guards))
    _log_calls(deps, identifier, judgment.records)
    score = compute_score(requirements, judgment.judgments, assets.analysis["score"])
    judgments = [
        {
            "requirement_id": j.requirement_id,
            "verdict": j.verdict,
            "reason": j.reason,
            "downgraded": j.downgraded,
            "citations": _citations(j, cards),
        }
        for j in judgment.judgments
    ]
    latency_ms = int((deps.clock() - int(item["created_epoch"])) * 1000)
    return advance(
        "judging",
        "done",
        {
            "judgments": judgments,
            "score": score,
            "latency_ms": latency_ms,
            "citations_dropped": judgment.citations_dropped,
            "downgraded": judgment.downgraded,
            "judge_retried": judgment.retried,
        },
    )


def process(identifier: str, deps: WorkerDeps, remaining_ms: Callable[[], int]) -> str:
    """Runs (or resumes) one analysis; returns done, failed or skipped."""
    now = deps.clock()
    item = deps.store.lease(identifier, now)
    if item is None:
        deps.log("Check already running or finished", check_id=identifier)
        return "skipped"

    cap = Decimal(deps.per_check_cap_micro) / MILLION
    budget = AnalysisBudget(cap, spent_usd=Decimal(int(item.get("cost_micro", 0))) / MILLION)
    guards = [budget, DeadlineGuard(remaining_ms)]
    final = False
    try:
        if deps.unavailable:
            raise AnalysisError(UNAVAILABLE, deps.unavailable)
        _run(identifier, item, deps, guards, budget)
        final = True
        deps.log("Check done", check_id=identifier, cost_micro=to_micro_usd(budget.spent_usd))
        return "done"
    except AnalysisError as error:
        final = True
        now = deps.clock()
        deps.store.fail(identifier, error.code, to_micro_usd(budget.spent_usd), now, iso(now))
        deps.log("Check failed", check_id=identifier, code=error.code, detail=error.detail[:300])
        return "failed"
    finally:
        # Only a final check settles: a crash leaves the reservation for the
        # redelivery (or, after the dead-letter queue, for the runbook).
        if final:
            deps.store.settle(identifier, item["spend_month"], int(item["reserved_micro"]), to_micro_usd(budget.spent_usd), iso(deps.clock()))
