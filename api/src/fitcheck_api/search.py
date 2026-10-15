"""The in-package vector index and the ranking POST /search serves, in pure Python (ADR 0005)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

CHUNK_KINDS = ("card", "summary", "claim")
# Which chunks rank a card under each RetrievalConfig.chunks setting.
CHUNK_SETS = {"card": ("card",), "claims": ("summary", "claim")}

# Requirement categories (fitcheck_api.cases.Requirement) and the card kinds
# that can be positive evidence; gap cards answer in a channel of their own.
CATEGORIES = ("skill", "experience", "education", "language", "location", "domain", "soft")
EVIDENCE_KINDS = frozenset({"project", "role", "skill", "profile", "education"})

# BM25 with the textbook parameters; not tuned.
BM25_K1 = 1.2
BM25_B = 0.75
TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be been by for from has have in into is it its of on or our such than that the their them "
    "they this to was we were will with within you your".split()
)


@dataclass(frozen=True)
class RetrievalConfig:
    """How the index ranks cards; config/retrieval.json holds the one served."""

    # "card": one vector per card (v0). "claims": one vector per claim plus one
    # for title and summary; a card scores its best chunk.
    chunks: str = "card"
    # Fuse the vector ranking with a BM25 ranking over the claims by
    # Reciprocal Rank Fusion with this k.
    hybrid: bool = False
    rrf_k: int = 60
    # Route each requirement category to the card kinds that can answer it
    # (allowed_kinds) and keep gap cards in a separate channel.
    routing: bool = False

    def __post_init__(self) -> None:
        if self.chunks not in CHUNK_SETS:
            raise ValueError(f"chunks must be one of {sorted(CHUNK_SETS)}, got {self.chunks!r}")
        for name in ("hybrid", "routing"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be true or false, got {getattr(self, name)!r}")
        if isinstance(self.rrf_k, bool) or not isinstance(self.rrf_k, int) or self.rrf_k < 1:
            raise ValueError(f"rrf_k must be a positive integer, got {self.rrf_k!r}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RetrievalConfig:
        unknown = set(data) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"unknown retrieval settings: {sorted(unknown)}")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def allowed_kinds(category: str | None) -> frozenset[str]:
    """Card kinds the evidence channel may return for a requirement category; any evidence kind without one."""
    if category is None:
        return EVIDENCE_KINDS
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    if category in ("location", "language"):
        return frozenset({"profile"})
    if category == "education":
        return frozenset({"education", "profile"})
    if category == "experience":
        # Years of experience are stated in a profile card.
        return EVIDENCE_KINDS - {"education"}
    return EVIDENCE_KINDS - {"profile", "education"}


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


def tokenize(text: str) -> list[str]:
    """Lowercase words and numbers, stopwords dropped, a plural -s stripped (workflows -> workflow)."""
    tokens = []
    for token in TOKEN.findall(text.lower()):
        if token in STOPWORDS:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.append(token)
    return tokens


class BM25:
    """Okapi BM25 over tokenized documents, with the always-positive idf ln(1 + (N - df + 0.5) / (df + 0.5))."""

    def __init__(self, documents: Sequence[Sequence[str]], k1: float = BM25_K1, b: float = BM25_B) -> None:
        self.k1 = k1
        self.b = b
        self.frequencies = [Counter(document) for document in documents]
        self.lengths = [len(document) for document in documents]
        self.average_length = sum(self.lengths) / len(documents) if documents else 0.0
        frequency = Counter(term for document in documents for term in set(document))
        count = len(documents)
        self.idf = {term: math.log(1 + (count - df + 0.5) / (df + 0.5)) for term, df in frequency.items()}

    def scores(self, query: Sequence[str]) -> list[float]:
        """One score per document, summed over the distinct query terms."""
        terms = [term for term in dict.fromkeys(query) if term in self.idf]
        results = []
        for frequencies, length in zip(self.frequencies, self.lengths):
            score = 0.0
            norm_length = self.k1 * (1 - self.b + self.b * length / self.average_length) if self.average_length else self.k1
            for term in terms:
                tf = frequencies.get(term, 0)
                if tf:
                    score += self.idf[term] * tf * (self.k1 + 1) / (tf + norm_length)
            results.append(score)
        return results


def keyword_text(card: Mapping[str, Any], chunk_text: str) -> str:
    """What BM25 reads for a claim chunk: the chunk ("<title>: <claim>") and the card's skills."""
    return f"{chunk_text}\n{' '.join(card['skills'])}"


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
    # BM25 over the claim chunks, whose positions in chunks are keyword_chunks.
    keyword_chunks: tuple[int, ...]
    keywords: BM25

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
        keyword_chunks = tuple(position for position, chunk in enumerate(chunks) if chunk.kind == "claim")
        keywords = BM25([tokenize(keyword_text(cards[chunks[position].card], chunks[position].text)) for position in keyword_chunks])
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
            keyword_chunks=keyword_chunks,
            keywords=keywords,
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


def card_keyword_scores(index: SearchIndex, query_text: str) -> list[float]:
    """Per card, the best BM25 score among its claim chunks (0 if no query term matches)."""
    best = [0.0] * len(index.cards)
    for position, score in zip(index.keyword_chunks, index.keywords.scores(tokenize(query_text))):
        card = index.chunks[position].card
        if score > best[card]:
            best[card] = score
    return best


def _rank(
    index: SearchIndex,
    candidates: Sequence[int],
    similarities: Sequence[float],
    keyword_scores: Sequence[float] | None,
    rrf_k: int,
) -> tuple[Hit, ...]:
    """Ranks the candidate cards by similarity, or by Reciprocal Rank Fusion of similarity and BM25."""

    def card_id(position: int) -> str:
        return index.cards[position]["id"]

    by_similarity = sorted(candidates, key=lambda position: (-similarities[position], card_id(position)))
    if keyword_scores is None:
        return tuple(Hit(index.cards[position], similarities[position], similarities[position]) for position in by_similarity)
    # A card no query term matches is left out of the BM25 ranking rather than ranked last.
    by_keywords = sorted(
        (position for position in candidates if keyword_scores[position] > 0),
        key=lambda position: (-keyword_scores[position], card_id(position)),
    )
    fused = dict.fromkeys(candidates, 0.0)
    for ranking in (by_similarity, by_keywords):
        for rank, position in enumerate(ranking, start=1):
            fused[position] += 1 / (rrf_k + rank)
    order = sorted(candidates, key=lambda position: (-fused[position], -similarities[position], card_id(position)))
    return tuple(Hit(index.cards[position], fused[position], similarities[position]) for position in order)


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
    keyword_scores = card_keyword_scores(index, query_text) if config.hybrid else None
    if not config.routing:
        candidates = range(len(index.cards))
        return SearchResult(evidence=_rank(index, candidates, similarities, keyword_scores, config.rrf_k))
    kinds = allowed_kinds(category)
    evidence = [position for position, card in enumerate(index.cards) if card["kind"] in kinds]
    gaps = [position for position, card in enumerate(index.cards) if card["kind"] == "gap"]
    return SearchResult(
        evidence=_rank(index, evidence, similarities, keyword_scores, config.rrf_k),
        gaps=_rank(index, gaps, similarities, keyword_scores, config.rrf_k),
    )
