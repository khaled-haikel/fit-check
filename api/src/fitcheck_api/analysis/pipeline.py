"""The whole analysis of one job description, from the phases in this package.

The worker (stage 3, PR B) runs the phases one at a time so it can resume;
evals/analysis.py runs them through analyze() or one by one. Both inject the
clients, the index and the guards.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..embeddings import Embedder
from ..search import SearchIndex
from .assets import Assets
from .costs import CallGuard, CallRecord
from .extract import ExtractionResult, Requirement, extract_requirements
from .judge import JudgeResult, judge_requirements
from .normalize import normalize_job_description
from .retrieve import Candidates, retrieve_candidates
from .score import compute_score


@dataclass
class AnalysisRun:
    requirements: list[Requirement]
    candidates: list[Candidates] = field(default_factory=list)
    judgment: JudgeResult | None = None
    extraction: ExtractionResult | None = None
    score: dict[str, Any] | None = None
    records: list[CallRecord] = field(default_factory=list)


def cards_by_id(index: SearchIndex) -> dict[str, dict[str, Any]]:
    return {card["id"]: card for card in index.cards}


def judge_with_retrieval(
    requirements: Sequence,
    assets: Assets,
    index: SearchIndex,
    claude_client: Any,
    embedder: Embedder,
    guards: Sequence[CallGuard],
) -> tuple[list[Candidates], JudgeResult, list[CallRecord]]:
    """Retrieval and judgment for given requirements (extracted, or labeled in the evaluation's judge mode)."""
    candidates, records = retrieve_candidates(
        requirements,
        index,
        embedder,
        int(assets.analysis["judge"]["evidence_k"]),
        assets.price(assets.embedding_model),
        guards,
    )
    judgment = judge_requirements(requirements, candidates, cards_by_id(index), assets, claude_client, guards)
    return candidates, judgment, [*records, *judgment.records]


def analyze(
    job_description: str,
    assets: Assets,
    index: SearchIndex,
    claude_client: Any,
    embedder: Embedder,
    guards: Sequence[CallGuard],
) -> AnalysisRun:
    extraction = extract_requirements(normalize_job_description(job_description), assets, claude_client, guards)
    candidates, judgment, records = judge_with_retrieval(extraction.requirements, assets, index, claude_client, embedder, guards)
    return AnalysisRun(
        requirements=extraction.requirements,
        candidates=candidates,
        judgment=judgment,
        extraction=extraction,
        score=compute_score(extraction.requirements, judgment.judgments, assets.analysis["score"]),
        records=[*extraction.records, *records],
    )
