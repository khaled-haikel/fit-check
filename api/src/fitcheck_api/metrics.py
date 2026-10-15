"""Retrieval metrics for the evaluation (evals/retrieval.py).

Per requirement, with the expected cards R and a ranking:
- hit@k = 1 if some card of R is in the top k, else 0
- recall@k = |R ∩ top-k| / |R|
- reciprocal rank = 1 / rank of the first card of R (0 if none is ranked)
All are macro-averaged over the requirements of a split.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

K_VALUES = (1, 3, 5)


def _check(relevant: set[str]) -> None:
    if not relevant:
        raise ValueError("the metrics need at least one expected card")


def hit_at_k(ranked_ids: Sequence[str], relevant: set[str], k: int) -> float:
    _check(relevant)
    return 1.0 if relevant.intersection(ranked_ids[:k]) else 0.0


def recall_at_k(ranked_ids: Sequence[str], relevant: set[str], k: int) -> float:
    _check(relevant)
    return len(relevant.intersection(ranked_ids[:k])) / len(relevant)


def reciprocal_rank(ranked_ids: Sequence[str], relevant: set[str]) -> float:
    for position, card_id in enumerate(ranked_ids, start=1):
        if card_id in relevant:
            return 1.0 / position
    return 0.0


def score_ranking(ranked_ids: Sequence[str], relevant: set[str]) -> dict[str, float]:
    scores = {}
    for k in K_VALUES:
        scores[f"hit@{k}"] = hit_at_k(ranked_ids, relevant, k)
        scores[f"recall@{k}"] = recall_at_k(ranked_ids, relevant, k)
    scores["mrr"] = reciprocal_rank(ranked_ids, relevant)
    return scores


def summarize(scores: Iterable[Mapping[str, float]]) -> dict[str, float]:
    rows = list(scores)
    if not rows:
        return {"n": 0}
    summary: dict[str, float] = {"n": len(rows)}
    for key in rows[0]:
        summary[key] = sum(row[key] for row in rows) / len(rows)
    return summary


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile, q in (0, 100]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[position - 1]


def regressions(
    current: Mapping[str, Mapping[str, float]],
    baseline: Mapping[str, Mapping[str, float]],
    metrics: Sequence[str] = ("hit@5", "recall@5"),
    tolerance: float = 0.02,
) -> list[str]:
    """Splits whose metrics fell by more than the tolerance against the baseline."""
    problems = []
    for split, base in baseline.items():
        if split not in current:
            problems.append(f"{split}: missing from the current run")
            continue
        for metric in metrics:
            if metric not in base:
                continue
            drop = base[metric] - current[split][metric]
            if drop > tolerance + 1e-12:
                problems.append(
                    f"{split}: {metric} fell from {base[metric]:.3f} to {current[split][metric]:.3f} (more than {tolerance})"
                )
    return problems
