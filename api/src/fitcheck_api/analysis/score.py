"""The fit score, computed only in code: weighted verdict values over the weights, 0 to 100."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

VERDICTS = ("meets", "partial", "does_not_meet")


def compute_score(requirements: Sequence, judgments: Sequence, config: Mapping[str, Any]) -> dict[str, Any]:
    """requirements: anything with id and type; judgments: anything with requirement_id and verdict."""
    weights = {kind: Decimal(str(weight)) for kind, weight in config["weights"].items()}
    values = {verdict: Decimal(str(value)) for verdict, value in config["values"].items()}
    verdicts = {judgment.requirement_id: judgment.verdict for judgment in judgments}
    counts = {kind: dict.fromkeys(VERDICTS, 0) for kind in ("must", "nice")}
    total = earned = Decimal(0)
    for requirement in requirements:
        verdict = verdicts.get(requirement.id, "does_not_meet")
        counts[requirement.type][verdict] += 1
        total += weights[requirement.type]
        earned += weights[requirement.type] * values[verdict]
    value = int((earned * 100 / total).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if total else 0
    return {"value": value, "must": counts["must"], "nice": counts["nice"]}
