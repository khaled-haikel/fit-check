import json

import pytest
from typing import get_args

from botocore.exceptions import ClientError
from indexes import card_index, make_index

from fitcheck_api import handler as handler_module
from fitcheck_api.cases import Requirement
from fitcheck_api.handler import SearchRequest, handler
from fitcheck_api.search import CATEGORIES, SearchIndex

MODEL = "test-embedding-model"


def _card(card_id: str, url: str | None = None, kind: str = "project") -> dict:
    proof = {"type": "repo", "url": url} if url else {"type": "none", "note": "private work"}
    return {
        "id": card_id,
        "kind": kind,
        "title": f"Title of {card_id}",
        "summary": f"Summary of {card_id}.",
        "claims": [f"Claim of {card_id}."],
        "skills": ["python"],
        "proof": [proof],
    }


def _index(placeholder: bool = False, model: str = MODEL) -> SearchIndex:
    if placeholder:
        return card_index([], [], model=model, placeholder=True, dimensions=3)
    cards = [_card("alpha", "https://example.com/a"), _card("beta"), _card("gamma")]
    return card_index(cards, [[1, 0, 0], [0, 1, 0], [0.6, 0.8, 0]], model=model)


class FakeEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.queries: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.queries.append(text)
        return self.vector


@pytest.fixture
def embedder(monkeypatch) -> FakeEmbedder:
    fake = FakeEmbedder([1, 0, 0])
    monkeypatch.setattr(handler_module, "_embedder", fake)
    monkeypatch.setattr(handler_module, "search_index", _index())
    monkeypatch.setenv("EMBEDDING_MODEL_ID", MODEL)
    return fake


def _search(http_event, lambda_context, body, request_id: str = "test-request-id") -> tuple[int, dict]:
    raw = body if isinstance(body, str) else json.dumps(body)
    response = handler(http_event("/search", method="POST", request_id=request_id, body=raw), lambda_context)
    return response["statusCode"], json.loads(response["body"])


def test_search_returns_the_closest_cards_first(embedder, http_event, lambda_context):
    status, body = _search(http_event, lambda_context, {"query": "terraform state", "k": 2})

    assert status == 200
    assert body["index_version"] == "test-version"
    assert [hit["id"] for hit in body["results"]] == ["alpha", "gamma"]
    assert [hit["score"] for hit in body["results"]] == [1.0, 0.6]
    assert embedder.queries == ["terraform state"]


def test_search_result_fields(embedder, http_event, lambda_context):
    _, body = _search(http_event, lambda_context, {"query": "anything", "k": 3})

    first, second = body["results"][0], body["results"][1]
    assert set(first) == {"id", "kind", "title", "summary", "skills", "proof", "verifiable", "score", "similarity"}
    assert first["verifiable"] is True
    assert first["similarity"] == first["score"] == 1.0
    # Without routing there is no gap channel.
    assert body["gap"] is None
    assert second["id"] == "gamma"
    assert body["results"][2]["verifiable"] is False


def test_search_defaults_to_five_results(embedder, http_event, lambda_context):
    _, body = _search(http_event, lambda_context, {"query": "anything"})

    assert len(body["results"]) == 3  # only three cards in the test index


def test_search_accepts_a_500_character_query(embedder, http_event, lambda_context):
    status, _ = _search(http_event, lambda_context, {"query": "a" * 500})

    assert status == 200


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "a" * 501},
        {"query": 42},
        {"query": "terraform", "k": 0},
        {"query": "terraform", "k": 11},
        {"query": "terraform", "k": "3"},
        {"query": "terraform", "k": 2.5},
        {"query": "terraform", "k": True},
        {"query": "terraform", "extra": 1},
        {"query": "terraform", "category": "salary"},
        {"query": "terraform", "category": 3},
    ],
    ids=[
        "missing-query",
        "empty-query",
        "blank-query",
        "query-too-long",
        "query-not-a-string",
        "k-too-small",
        "k-too-large",
        "k-as-string",
        "k-as-float",
        "k-as-bool",
        "unknown-field",
        "unknown-category",
        "category-not-a-string",
    ],
)
def test_search_rejects_invalid_input(embedder, http_event, lambda_context, body):
    status, response = _search(http_event, lambda_context, body)

    assert status == 400
    assert response["error"] == "invalid request"
    assert response["details"]
    assert embedder.queries == []


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '"text"'], ids=["not-json", "array", "string"])
def test_search_rejects_a_body_that_is_not_a_json_object(embedder, http_event, lambda_context, raw):
    status, response = _search(http_event, lambda_context, raw)

    assert status == 400
    assert response == {"error": "the body must be a JSON object"}


@pytest.mark.parametrize(
    "index",
    [None, _index(placeholder=True), _index(model="another-model")],
    ids=["no-index", "placeholder", "other-model"],
)
def test_search_refuses_without_a_usable_index(monkeypatch, embedder, http_event, lambda_context, log_stream, index):
    monkeypatch.setattr(handler_module, "search_index", index)

    status, response = _search(http_event, lambda_context, {"query": "terraform"})

    assert status == 503
    assert response == {"error": "search is unavailable"}
    assert embedder.queries == []
    records = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    assert any(record["message"] == "Search unavailable" for record in records)


def test_search_logs_a_truncated_query_and_latencies(embedder, http_event, lambda_context, log_stream):
    query = "q" * 300

    _search(http_event, lambda_context, {"query": query, "k": 1}, request_id="req-7")

    records = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    search_logs = [record for record in records if record["message"] == "Search"]
    assert len(search_logs) == 1
    log = search_logs[0]
    assert log["correlation_id"] == "req-7"
    assert log["query"] == "q" * 200
    assert log["k"] == 1
    assert log["embedding_ms"] >= 0
    assert log["search_ms"] >= 0


def test_search_route_does_not_accept_get(embedder, http_event, lambda_context):
    response = handler(http_event("/search"), lambda_context)

    assert response["statusCode"] in (404, 405)


class FailingEmbedder:
    def embed(self, text: str) -> list[float]:
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "Too many requests"}}, "InvokeModel")


def test_search_returns_503_when_the_embedding_call_fails(monkeypatch, embedder, http_event, lambda_context, log_stream):
    monkeypatch.setattr(handler_module, "_embedder", FailingEmbedder())

    status, response = _search(http_event, lambda_context, {"query": "terraform"})

    assert status == 503
    assert response == {"error": "search is unavailable"}
    records = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    assert any(record["message"] == "Embedding failed" and "ThrottlingException" in record.get("exception", "") for record in records)


def _routed_index() -> SearchIndex:
    cards = [
        _card("alpha", "https://example.com/a"),
        _card("profile-place", kind="profile"),
        _card("gap-far", kind="gap"),
        _card("gap-near", kind="gap"),
    ]
    vectors = [[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0], [0.8, 0.6, 0]]
    chunks = [(card["id"], "summary", vector) for card, vector in zip(cards, vectors)]
    return make_index(cards, chunks, retrieval={"chunks": "claims", "routing": True}, model=MODEL)


@pytest.fixture
def routed(monkeypatch, embedder) -> FakeEmbedder:
    monkeypatch.setattr(handler_module, "search_index", _routed_index())
    return embedder


def test_gaps_answer_apart_from_the_evidence(routed, http_event, lambda_context):
    status, body = _search(http_event, lambda_context, {"query": "terraform"})

    assert status == 200
    assert [hit["id"] for hit in body["results"]] == ["alpha", "profile-place"]
    assert body["gap"]["id"] == "gap-near"
    assert body["gap"]["kind"] == "gap"
    assert body["gap"]["similarity"] == 0.8


def test_category_routes_the_evidence(routed, http_event, lambda_context):
    _, location = _search(http_event, lambda_context, {"query": "remote from Brazil", "category": "location"})
    _, skill = _search(http_event, lambda_context, {"query": "terraform", "category": "skill"})

    assert [hit["id"] for hit in location["results"]] == ["profile-place"]
    assert [hit["id"] for hit in skill["results"]] == ["alpha"]
    assert location["gap"]["id"] == skill["gap"]["id"] == "gap-near"


def test_search_logs_the_category(routed, http_event, lambda_context, log_stream):
    _search(http_event, lambda_context, {"query": "terraform", "category": "skill"})

    records = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    assert [record["category"] for record in records if record["message"] == "Search"] == ["skill"]


def test_the_api_accepts_the_categories_the_cases_use():
    api = get_args(get_args(SearchRequest.model_fields["category"].annotation)[0])
    cases = get_args(Requirement.model_fields["category"].annotation)

    assert set(api) == set(cases) == set(CATEGORIES)
