import math

import pytest
from indexes import card_index, make_index

from fitcheck_api.search import (
    BM25,
    RetrievalConfig,
    SearchIndex,
    card_chunks,
    card_text,
    cosine,
    index_document,
    index_version,
    is_verifiable,
    search,
    tokenize,
)


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


def _ids(result) -> list[str]:
    return [hit.card["id"] for hit in result.evidence]


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


def test_card_ranking_orders_by_similarity_and_breaks_ties_by_id():
    cards = [_card(card_id) for card_id in ("c", "b", "a", "d")]
    index = card_index(cards, [[0, 1], [1, 0], [1, 0], [1, 1]])

    result = search(index, [1, 0])

    assert _ids(result) == ["a", "b", "d", "c"]
    assert [round(hit.similarity, 4) for hit in result.evidence] == [1.0, 1.0, 0.7071, 0.0]
    assert all(hit.score == hit.similarity for hit in result.evidence)
    assert result.gaps == ()


def test_ranking_does_not_assume_normalized_vectors():
    index = card_index([_card("near"), _card("far")], [[10, 1], [0.1, 0.9]])

    assert _ids(search(index, [5, 0])) == ["near", "far"]


def test_a_card_scores_its_best_chunk():
    cards = [_card("one-strong-claim"), _card("all-medium")]
    index = make_index(
        cards,
        [
            ("one-strong-claim", "card", [0, 1]),
            ("one-strong-claim", "summary", [0, 1]),
            ("one-strong-claim", "claim", [1, 0]),
            ("one-strong-claim", "claim", [0, 1]),
            ("all-medium", "card", [1, 1]),
            ("all-medium", "summary", [1, 1]),
            ("all-medium", "claim", [1, 1]),
        ],
    )

    by_claims = search(index, [1, 0], config=RetrievalConfig(chunks="claims"))
    by_card = search(index, [1, 0], config=RetrievalConfig(chunks="card"))

    assert _ids(by_claims) == ["one-strong-claim", "all-medium"]
    assert by_claims.evidence[0].similarity == pytest.approx(1.0)
    # The whole-card vectors ignore claims: v0 prefers the other card.
    assert _ids(by_card) == ["all-medium", "one-strong-claim"]


def test_search_uses_the_index_configuration_by_default():
    cards = [_card("a"), _card("b")]
    index = make_index(
        cards,
        [("a", "card", [1, 0]), ("a", "summary", [0, 1]), ("b", "card", [0, 1]), ("b", "summary", [1, 0])],
        retrieval={"chunks": "claims"},
    )

    assert _ids(search(index, [1, 0])) == ["b", "a"]


def test_search_rejects_a_query_of_the_wrong_dimension():
    with pytest.raises(ValueError):
        search(card_index([_card("a")], [[1, 0]]), [1, 0, 0])


def test_index_rejects_mismatched_chunks_and_vectors():
    data = {
        "index_version": "v",
        "model": "m",
        "dimensions": 2,
        "cards": [_card("a")],
        "chunks": [{"card": "a", "kind": "card", "text": "a"}, {"card": "a", "kind": "claim", "text": "a"}],
        "vectors": [[1, 0]],
    }
    with pytest.raises(ValueError, match="2 chunks but 1 vectors"):
        SearchIndex.from_dict(data)


def test_index_rejects_vectors_of_the_wrong_dimension():
    with pytest.raises(ValueError, match="3 dimensions, expected 2"):
        make_index([_card("a")], [("a", "card", [1, 0, 0])], dimensions=2)


def test_index_rejects_chunks_of_unknown_cards_or_kinds():
    with pytest.raises(ValueError, match="unknown card"):
        make_index([_card("a")], [("b", "card", [1, 0])])
    with pytest.raises(ValueError, match="unknown kind"):
        make_index([_card("a")], [("a", "paragraph", [1, 0])])


def test_retrieval_config_rejects_unknown_settings_and_values():
    with pytest.raises(ValueError, match="unknown retrieval settings"):
        RetrievalConfig.from_dict({"chunks": "card", "rerank": True})
    with pytest.raises(ValueError, match="chunks must be one of"):
        RetrievalConfig(chunks="sentences")
    with pytest.raises(ValueError, match="hybrid must be"):
        RetrievalConfig(hybrid="yes")
    with pytest.raises(ValueError, match="rrf_k must be"):
        RetrievalConfig(rrf_k=0)


def test_card_chunks_put_the_title_in_front_of_each_claim():
    chunks = card_chunks(_card("a"))

    assert [(chunk["kind"], chunk["text"]) for chunk in chunks] == [
        ("card", card_text(_card("a"))),
        ("summary", "Title a: Summary a."),
        ("claim", "Title a: First claim."),
        ("claim", "Title a: Second claim."),
    ]
    assert {chunk["card"] for chunk in chunks} == {"a"}


def test_index_version_ignores_card_order_and_tracks_model_content_and_config():
    cards = [_card("a"), _card("b")]
    config = RetrievalConfig(chunks="claims")
    version = index_version(index_document(cards, "m", 1024, config))

    assert version == index_version(index_document(list(reversed(cards)), "m", 1024, config))
    assert version != index_version(index_document(cards, "other", 1024, config))
    assert version != index_version(index_document(cards, "m", 512, config))
    assert version != index_version(index_document(cards, "m", 1024, RetrievalConfig(chunks="card")))
    assert version != index_version(index_document([_card("a"), _card("b", summary="Changed.")], "m", 1024, config))


def test_card_text_includes_title_summary_claims_and_skills():
    text = card_text(_card("a"))

    assert text.splitlines() == ["Title a", "Summary a.", "First claim.", "Second claim.", "Skills: python, aws"]


def test_verifiable_needs_a_proof_with_a_url():
    assert not is_verifiable(_card("a"))
    assert is_verifiable(_card("a", proof=[{"type": "none"}, {"type": "repo", "url": "https://example.com"}]))


def test_tokenize_lowercases_drops_stopwords_and_strips_plurals():
    assert tokenize("Workflows, APIs and CI/CD for the AWS class of Kubernetes.") == [
        "workflow",
        "api",
        "ci",
        "cd",
        "aws",
        "class",
        "kubernete",
    ]


def test_bm25_matches_a_hand_computed_corpus():
    # N = 3, df(terraform) = 2, idf = ln(1 + 1.5 / 2.5) = ln 1.6, average length 7/3.
    bm25 = BM25([["terraform", "state"], ["docker"], ["terraform", "terraform", "docker", "kubernetes"]])
    idf = math.log(1.6)
    first = idf * 1 * 2.2 / (1 + 1.2 * (0.25 + 0.75 * 2 / (7 / 3)))
    third = idf * 2 * 2.2 / (2 + 1.2 * (0.25 + 0.75 * 4 / (7 / 3)))

    scores = bm25.scores(["terraform", "terraform", "unknown"])

    assert scores == pytest.approx([first, 0.0, third])
    assert scores == pytest.approx([0.499176, 0.0, 0.538145], abs=1e-6)


def _hybrid_index():
    cards = [
        _card("vector-best", title="Cloud work", claims=["Serverless deployments."], skills=["aws"]),
        _card("keyword-best", title="Infra", claims=["Terraform modules with remote state."], skills=["terraform"]),
        _card("neither", title="Frontend", claims=["Component libraries."], skills=["react"]),
    ]
    chunks = []
    for card, vector in zip(cards, ([1, 0], [0.6, 0.8], [0, 1])):
        chunks.append((card["id"], "summary", vector, f"{card['title']}: {card['summary']}"))
        chunks += [(card["id"], "claim", vector, f"{card['title']}: {claim}") for claim in card["claims"]]
    return make_index(cards, chunks, retrieval={"chunks": "claims", "hybrid": True, "rrf_k": 60})


def test_hybrid_fuses_vector_and_keyword_ranks():
    result = search(_hybrid_index(), [1, 0], "terraform remote state")

    # vector ranks: vector-best 1, keyword-best 2, neither 3; BM25 ranks: keyword-best 1 only.
    assert _ids(result) == ["keyword-best", "vector-best", "neither"]
    scores = {hit.card["id"]: hit.score for hit in result.evidence}
    assert scores["keyword-best"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["vector-best"] == pytest.approx(1 / 61)
    assert scores["neither"] == pytest.approx(1 / 63)
    # similarity stays the cosine, whatever the fused order.
    assert result.evidence[1].similarity == pytest.approx(1.0)


def test_a_card_no_query_term_matches_gets_no_keyword_rank():
    result = search(_hybrid_index(), [1, 0], "kotlin")

    assert _ids(result) == ["vector-best", "keyword-best", "neither"]
    assert [hit.score for hit in result.evidence] == pytest.approx([1 / 61, 1 / 62, 1 / 63])


def test_skills_are_searchable_by_keyword():
    result = search(_hybrid_index(), [0, 1], "react")

    assert result.evidence[0].card["id"] == "neither"
    assert result.evidence[0].score == pytest.approx(2 / 61)


def test_vector_only_ignores_the_query_text():
    index = _hybrid_index()

    result = search(index, [1, 0], "terraform remote state", config=RetrievalConfig(chunks="claims"))

    assert _ids(result) == ["vector-best", "keyword-best", "neither"]
