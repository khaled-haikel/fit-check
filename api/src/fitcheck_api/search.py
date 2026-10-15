"""The in-package vector index and cosine search, in pure Python (ADR 0005)."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def card_text(card: Mapping[str, Any]) -> str:
    """The text embedded for a card. The title is included on purpose: it helps short queries."""
    lines = [card["title"], card["summary"], *card["claims"], "Skills: " + ", ".join(card["skills"])]
    return "\n".join(lines)


def index_version(cards: Sequence[Mapping[str, Any]], model: str, dimensions: int) -> str:
    """sha256 of the canonical cards, the model and the dimensions; no dates, so equal input gives an equal hash."""
    canonical = json.dumps(
        {"cards": sorted(cards, key=lambda card: card["id"]), "model": model, "dimensions": dimensions},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_verifiable(card: Mapping[str, Any]) -> bool:
    return any(proof.get("url") for proof in card["proof"])


def norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vectors differ in length: {len(a)} and {len(b)}")
    denominator = norm(a) * norm(b)
    if denominator == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / denominator


@dataclass(frozen=True)
class SearchIndex:
    index_version: str
    model: str
    dimensions: int
    placeholder: bool
    cards: tuple[dict[str, Any], ...]
    vectors: tuple[tuple[float, ...], ...]
    norms: tuple[float, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchIndex:
        cards = tuple(data["cards"])
        vectors = tuple(tuple(float(value) for value in vector) for vector in data["vectors"])
        dimensions = int(data["dimensions"])
        if len(cards) != len(vectors):
            raise ValueError(f"index has {len(cards)} cards but {len(vectors)} vectors")
        for card, vector in zip(cards, vectors):
            if len(vector) != dimensions:
                raise ValueError(f"vector for {card['id']} has {len(vector)} dimensions, expected {dimensions}")
        return cls(
            index_version=str(data["index_version"]),
            model=str(data["model"]),
            dimensions=dimensions,
            placeholder=bool(data.get("placeholder", False)),
            cards=cards,
            vectors=vectors,
            norms=tuple(norm(vector) for vector in vectors),
        )


def load_index(path: Path) -> SearchIndex:
    return SearchIndex.from_dict(json.loads(path.read_text(encoding="utf-8")))


def rank(index: SearchIndex, query_vector: Sequence[float]) -> list[tuple[dict[str, Any], float]]:
    """Every card with its cosine score, best first; ties break by card id so the order is stable."""
    if len(query_vector) != index.dimensions:
        raise ValueError(f"query has {len(query_vector)} dimensions, index has {index.dimensions}")
    query_norm = norm(query_vector)
    scored = []
    for card, vector, card_norm in zip(index.cards, index.vectors, index.norms):
        denominator = query_norm * card_norm
        score = sum(x * y for x, y in zip(query_vector, vector)) / denominator if denominator else 0.0
        scored.append((card, score))
    scored.sort(key=lambda item: (-item[1], item[0]["id"]))
    return scored


def top_k(index: SearchIndex, query_vector: Sequence[float], k: int) -> list[tuple[dict[str, Any], float]]:
    return rank(index, query_vector)[:k]
