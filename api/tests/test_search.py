import math

import pytest

from fitcheck_api.search import SearchIndex, card_text, cosine, index_version, is_verifiable, rank, top_k


def _card(card_id: str, **overrides) -> dict:
    card = {
        "id": card_id,
        "kind": "skill",
        "title": f"Title {card_id}",
        "summary": f"Summary {card_id}.",
        "claims": ["First claim.", "Second claim."],
        "skills": ["python", "aws"],
        "proof": [{"type": "none"}],
    }
    card.update(overrides)
    return card


def _index(vectors: dict[str, list[float]]) -> SearchIndex:
    return SearchIndex.from_dict(
        {
            "index_version": "v",
            "model": "m",
            "dimensions": 2,
            "cards": [_card(card_id) for card_id in vectors],
            "vectors": list(vectors.values()),
        }
    )


def test_cosine_of_known_vectors():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine([3, 4], [6, 8]) == pytest.approx(1.0)
    assert cosine([1, 1], [1, 0]) == pytest.approx(1 / math.sqrt(2))


def test_cosine_of_a_zero_vector_is_zero():
    assert cosine([0, 0], [1, 0]) == 0.0


def test_cosine_rejects_different_lengths():
    with pytest.raises(ValueError):
        cosine([1, 0], [1, 0, 0])


def test_rank_orders_by_score_and_breaks_ties_by_id():
    index = _index({"c": [0, 1], "b": [1, 0], "a": [1, 0], "d": [1, 1]})

    ranking = rank(index, [1, 0])

    assert [card["id"] for card, _ in ranking] == ["a", "b", "d", "c"]
    assert [round(score, 4) for _, score in ranking] == [1.0, 1.0, 0.7071, 0.0]


def test_rank_does_not_assume_normalized_vectors():
    index = _index({"near": [10, 1], "far": [0.1, 0.9]})

    assert [card["id"] for card, _ in rank(index, [5, 0])] == ["near", "far"]


def test_top_k_cuts_the_ranking():
    index = _index({"a": [1, 0], "b": [0.9, 0.1], "c": [0, 1]})

    assert [card["id"] for card, _ in top_k(index, [1, 0], 2)] == ["a", "b"]
    assert len(top_k(index, [1, 0], 10)) == 3


def test_rank_rejects_a_query_of_the_wrong_dimension():
    with pytest.raises(ValueError):
        rank(_index({"a": [1, 0]}), [1, 0, 0])


def test_index_rejects_mismatched_cards_and_vectors():
    with pytest.raises(ValueError, match="2 cards but 1 vectors"):
        SearchIndex.from_dict(
            {"index_version": "v", "model": "m", "dimensions": 2, "cards": [_card("a"), _card("b")], "vectors": [[1, 0]]}
        )


def test_index_rejects_vectors_of_the_wrong_dimension():
    with pytest.raises(ValueError, match="3 dimensions, expected 2"):
        SearchIndex.from_dict({"index_version": "v", "model": "m", "dimensions": 2, "cards": [_card("a")], "vectors": [[1, 0, 0]]})


def test_index_version_ignores_card_order_and_tracks_model_and_content():
    cards = [_card("a"), _card("b")]
    version = index_version(cards, "m", 1024)

    assert version == index_version(list(reversed(cards)), "m", 1024)
    assert version != index_version(cards, "other", 1024)
    assert version != index_version(cards, "m", 512)
    assert version != index_version([_card("a"), _card("b", summary="Changed.")], "m", 1024)


def test_card_text_includes_title_summary_claims_and_skills():
    text = card_text(_card("a"))

    assert text.splitlines() == ["Title a", "Summary a.", "First claim.", "Second claim.", "Skills: python, aws"]


def test_verifiable_needs_a_proof_with_a_url():
    assert not is_verifiable(_card("a"))
    assert is_verifiable(_card("a", proof=[{"type": "none"}, {"type": "repo", "url": "https://example.com"}]))
