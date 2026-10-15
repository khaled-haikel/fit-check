"""Retrieval: the evidence and gap candidates for each requirement, with the search /search serves."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..embeddings import Embedder, Embedding
from ..search import SearchIndex, search
from .costs import CallGuard, CallRecord, ModelPrice, guarded_call, usage_cost, worst_case_cost

PHASE = "retrieve"


@dataclass(frozen=True)
class Candidates:
    requirement_id: str
    # The best evidence cards, best first.
    evidence: tuple[str, ...]
    # The closest gap card, if the index keeps gaps apart.
    gap: str | None

    @property
    def allowed(self) -> tuple[str, ...]:
        return (*self.evidence, self.gap) if self.gap else self.evidence


def embed_guarded(embedder: Embedder, text: str, price: ModelPrice, guards: Sequence[CallGuard], phase: str = PHASE) -> tuple[Embedding, CallRecord]:
    """One Titan call between the guards, one at a time like the rest of the project."""
    is_cached = getattr(embedder.client, "is_cached", None)
    cached = bool(is_cached(embedder.model_id, embedder.request_body(text))) if is_cached else False

    def call() -> tuple[Embedding, CallRecord]:
        embedding = embedder.embed_detailed(text)
        record = CallRecord(
            phase=phase,
            model=embedder.model_id,
            attempt=1,
            input_tokens=embedding.input_tokens,
            output_tokens=0,
            cache_write_tokens=0,
            cache_read_tokens=0,
            latency_ms=embedding.latency_ms,
            stop_reason=None,
            request_id=embedding.request_id,
            retry_attempts=embedding.retry_attempts,
            cached=embedding.cached,
            cost_usd=usage_cost(price, embedding.input_tokens, 0),
        )
        return embedding, record

    return guarded_call(guards, phase, worst_case_cost(price, len(text), 0), cached, call, lambda result: result[1])


def retrieve_candidates(
    requirements: Sequence,
    index: SearchIndex,
    embedder: Embedder,
    evidence_k: int,
    price: ModelPrice,
    guards: Sequence[CallGuard],
) -> tuple[list[Candidates], list[CallRecord]]:
    """requirements: anything with id, text_en and category (extracted or labeled)."""
    candidates, records = [], []
    for requirement in requirements:
        embedding, record = embed_guarded(embedder, requirement.text_en, price, guards)
        records.append(record)
        result = search(index, embedding.vector, requirement.text_en, requirement.category)
        candidates.append(
            Candidates(
                requirement_id=requirement.id,
                evidence=tuple(hit.card["id"] for hit in result.evidence[:evidence_k]),
                gap=result.gaps[0].card["id"] if result.gaps else None,
            )
        )
    return candidates, records
