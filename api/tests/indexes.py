"""Builds small in-memory search indexes for the tests."""

from fitcheck_api.search import SearchIndex


def make_index(
    cards: list[dict],
    chunks: list[tuple],
    retrieval: dict | None = None,
    model: str = "test-embedding-model",
    placeholder: bool = False,
    dimensions: int | None = None,
) -> SearchIndex:
    """chunks: (card id, kind, vector) or (card id, kind, vector, text)."""
    entries = [chunk if len(chunk) == 4 else (*chunk, f"{chunk[1]} of {chunk[0]}") for chunk in chunks]
    vectors = [vector for _, _, vector, _ in entries]
    return SearchIndex.from_dict(
        {
            "index_version": "test-version",
            "model": model,
            "dimensions": dimensions or (len(vectors[0]) if vectors else 3),
            "placeholder": placeholder,
            "retrieval": retrieval if retrieval is not None else {"chunks": "card"},
            "cards": cards,
            "chunks": [{"card": card_id, "kind": kind, "text": text} for card_id, kind, _, text in entries],
            "vectors": vectors,
        }
    )


def card_index(cards: list[dict], vectors: list[list[float]], **kwargs) -> SearchIndex:
    """One whole-card chunk per card, ranked as v0."""
    return make_index(cards, [(card["id"], "card", vector) for card, vector in zip(cards, vectors)], **kwargs)
