"""The in-package vector index and the ranking POST /search serves, in pure Python (ADR 0005)."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

CHUNK_KINDS = ("card", "summary", "claim")
# Which chunks rank a card under each RetrievalConfig.chunks setting.
CHUNK_SETS = {"card": ("card",), "claims": ("summary", "claim")}


@dataclass(frozen=True)
class RetrievalConfig:
    """How the index ranks cards; config/retrieval.json holds the one served."""

    # "card": one vector per card (v0). "claims": one vector per claim plus one
    # for title and summary; a card scores its best chunk.
    chunks: str = "card"

    def __post_init__(self) -> None:
        if self.chunks not in CHUNK_SETS:
            raise ValueError(f"chunks must be one of {sorted(CHUNK_SETS)}, got {self.chunks!r}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RetrievalConfig:
        unknown = set(data) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"unknown retrieval settings: {sorted(unknown)}")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def card_text(card: Mapping[str, Any]) -> str:
    """The whole card as one text (v0). The title is included on purpose: it helps short queries."""
    lines = [card["title"], card["summary"], *card["claims"], "Skills: " + ", ".join(card["skills"])]
    return "\n".join(lines)


def card_chunks(card: Mapping[str, Any]) -> list[dict[str, str]]:
    """Every text the index embeds for a card. Claims carry the title, so each chunk names its project."""
    title = card["title"]
    chunks = [
        {"card": card["id"], "kind": "card", "text": card_text(card)},
        {"card": card["id"], "kind": "summary", "text": f"{title}: {card['summary']}"},
    ]
    chunks += [{"card": card["id"], "kind": "claim", "text": f"{title}: {claim}"} for claim in card["claims"]]
    return chunks


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def index_document(cards: Iterable[Mapping[str, Any]], model: str, dimensions: int, config: RetrievalConfig) -> dict[str, Any]:
    """Everything in the index except the vectors, which the embedding model derives from it."""
    ordered = sorted(cards, key=lambda card: card["id"])
    return {
        "model": model,
        "dimensions": dimensions,
        "retrieval": config.to_dict(),
        "cards": ordered,
        "chunks": [chunk for card in ordered for chunk in card_chunks(card)],
    }


def index_version(document: Mapping[str, Any]) -> str:
    """sha256 of the index document; no dates, so equal input gives an equal version on any machine."""
    return hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()


def is_verifiable(card: Mapping[str, Any]) -> bool:
    return any(proof.get("url") for proof in card["proof"])


def norm(vector: Sequence[float]) -> float:
    return math.sqrt(math.sumprod(vector, vector))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vectors differ in length: {len(a)} and {len(b)}")
    denominator = norm(a) * norm(b)
    if denominator == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / denominator


@dataclass(frozen=True)
class Chunk:
    card: int  # position of the card in SearchIndex.cards
    kind: str
    text: str


@dataclass(frozen=True)
class SearchIndex:
    index_version: str
    model: str
    dimensions: int
    placeholder: bool
    config: RetrievalConfig
    cards: tuple[dict[str, Any], ...]
    chunks: tuple[Chunk, ...]
    vectors: tuple[tuple[float, ...], ...]
    norms: tuple[float, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchIndex:
        cards = tuple(data["cards"])
        positions = {card["id"]: position for position, card in enumerate(cards)}
        chunks = []
        for chunk in data["chunks"]:
            if chunk["card"] not in positions:
                raise ValueError(f"chunk of unknown card {chunk['card']!r}")
            if chunk["kind"] not in CHUNK_KINDS:
                raise ValueError(f"chunk of unknown kind {chunk['kind']!r}")
            chunks.append(Chunk(card=positions[chunk["card"]], kind=chunk["kind"], text=chunk["text"]))
        # JSON numbers already load as float (or int); tuple() keeps the cold start short.
        vectors = tuple(map(tuple, data["vectors"]))
        dimensions = int(data["dimensions"])
        if len(chunks) != len(vectors):
            raise ValueError(f"index has {len(chunks)} chunks but {len(vectors)} vectors")
        for chunk, vector in zip(chunks, vectors):
            if len(vector) != dimensions:
                raise ValueError(f"vector for a chunk of {cards[chunk.card]['id']} has {len(vector)} dimensions, expected {dimensions}")
        return cls(
            index_version=str(data["index_version"]),
            model=str(data["model"]),
            dimensions=dimensions,
            placeholder=bool(data.get("placeholder", False)),
            config=RetrievalConfig.from_dict(data.get("retrieval", {})),
            cards=cards,
            chunks=tuple(chunks),
            vectors=vectors,
            norms=tuple(norm(vector) for vector in vectors),
        )


def load_index(path: Path) -> SearchIndex:
    return SearchIndex.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Hit:
    card: dict[str, Any]
    # What the results are ordered by.
    score: float
    # Cosine similarity between the query and the card's best chunk.
    similarity: float


@dataclass(frozen=True)
class SearchResult:
    evidence: tuple[Hit, ...]
    # The gap cards, best first, when they are kept apart from the evidence.
    gaps: tuple[Hit, ...] = ()


def card_similarities(index: SearchIndex, query_vector: Sequence[float], kinds: Sequence[str]) -> list[float]:
    """Per card, the best cosine similarity among its chunks of the given kinds (-1 if it has none)."""
    if len(query_vector) != index.dimensions:
        raise ValueError(f"query has {len(query_vector)} dimensions, index has {index.dimensions}")
    query_norm = norm(query_vector)
    best = [-1.0] * len(index.cards)
    for chunk, vector, chunk_norm in zip(index.chunks, index.vectors, index.norms):
        if chunk.kind not in kinds:
            continue
        denominator = query_norm * chunk_norm
        # math.sumprod is the fastest dot product in the standard library; this loop is the hot path.
        similarity = math.sumprod(query_vector, vector) / denominator if denominator else 0.0
        if similarity > best[chunk.card]:
            best[chunk.card] = similarity
    return best


def search(
    index: SearchIndex,
    query_vector: Sequence[float],
    query_text: str = "",
    category: str | None = None,
    config: RetrievalConfig | None = None,
) -> SearchResult:
    """The ranking POST /search serves and evals/retrieval.py measures; config defaults to the index's."""
    config = config or index.config
    similarities = card_similarities(index, query_vector, CHUNK_SETS[config.chunks])
    order = sorted(range(len(index.cards)), key=lambda position: (-similarities[position], index.cards[position]["id"]))
    return SearchResult(evidence=tuple(Hit(index.cards[position], similarities[position], similarities[position]) for position in order))
