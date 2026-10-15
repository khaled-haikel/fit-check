"""evals/retrieval.py: which requirements each channel measures, and how they are scored."""

import importlib.util
import sys
from pathlib import Path

import pytest

from fitcheck_api.cases import Case
from fitcheck_api.content import Content
from fitcheck_api.evidence import Card
from fitcheck_api.search import SearchIndex

SCRIPT = Path(__file__).resolve().parents[2] / "evals" / "retrieval.py"


@pytest.fixture(scope="module")
def retrieval():
    spec = importlib.util.spec_from_file_location("retrieval_eval", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up while the script loads.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


def _card(card_id: str, kind: str = "project") -> Card:
    return Card(
        id=card_id,
        kind=kind,
        title=f"Title {card_id}",
        summary=f"Summary {card_id}.",
        claims=[f"Claim {card_id}."],
        skills=["python"],
        proof=[{"type": "none"}],
    )


def _requirement(requirement_id: str, verdict: str, evidence: list[str], category: str = "skill") -> dict:
    return {
        "id": requirement_id,
        "text": f"Text {requirement_id}",
        "text_en": f"Text {requirement_id}",
        "type": "must",
        "category": category,
        "verdict": verdict,
        "evidence": evidence,
    }


def _case(case_id: str, split: str, requirements: list[dict]) -> Case:
    return Case(
        id=case_id,
        split=split,
        language="en",
        source="synthetic",
        collected="2026-01-01",
        role_title="Role",
        job_description="Description.",
        requirements=requirements,
    )


@pytest.fixture
def content() -> Content:
    cards = {card.id: card for card in [_card("alpha"), _card("beta"), _card("gap-one", "gap"), _card("gap-two", "gap")]}
    cases = {
        "dev-01": _case(
            "dev-01",
            "dev",
            [
                _requirement("r01", "meets", ["alpha"]),
                _requirement("r02", "partial", ["beta", "gap-one"]),
                _requirement("r03", "does_not_meet", ["gap-two"]),
                _requirement("r04", "does_not_meet", []),
            ],
        ),
        "test-01": _case("test-01", "test", [_requirement("r01", "meets", ["alpha", "beta"])]),
        "special-01": _case("special-01", "special", [_requirement("r01", "meets", ["alpha"])]),
    }
    return Content(cards=cards, cases=cases)


def test_channels_select_their_requirements(retrieval, content):
    queries = {(query.case, query.requirement): query for query in retrieval.select_queries(content, ("dev", "test"))}

    assert set(queries) == {("dev-01", "r01"), ("dev-01", "r02"), ("dev-01", "r03"), ("test-01", "r01")}
    assert queries[("dev-01", "r01")].evidence == {"alpha"} and queries[("dev-01", "r01")].gaps == set()
    # A partial verdict keeps its positive cards in the evidence channel and its gap in the gap channel.
    assert queries[("dev-01", "r02")].evidence == {"beta"} and queries[("dev-01", "r02")].gaps == {"gap-one"}
    # does_not_meet never measures the evidence channel.
    assert queries[("dev-01", "r03")].evidence == set() and queries[("dev-01", "r03")].gaps == {"gap-two"}


def test_splits_not_asked_for_are_never_queried(retrieval, content):
    queries = retrieval.select_queries(content, ("dev",))

    assert {query.split for query in queries} == {"dev"}


def test_parse_splits(retrieval):
    assert retrieval.parse_splits("dev") == ("dev",)
    assert retrieval.parse_splits("dev, test") == ("dev", "test")
    with pytest.raises(Exception):
        retrieval.parse_splits("special")


def test_evaluate_scores_the_evidence_channel(retrieval, content):
    index = SearchIndex.from_dict(
        {
            "index_version": "v",
            "model": "m",
            "dimensions": 2,
            "cards": [card.model_dump(mode="json", exclude_none=True) for card in content.cards.values()],
            "vectors": [[1, 0], [0, 1], [0.9, 0.1], [0.1, 0.9]],
        }
    )
    queries = [query for query in retrieval.select_queries(content, ("dev",)) if query.requirement in ("r01", "r02")]
    vectors = {"Text r01": [1, 0], "Text r02": [1, 0]}

    rows, timings = retrieval.evaluate(index, queries, vectors, {})

    assert len(timings) == 2
    first, second = rows
    assert first["evidence"]["expected"] == {"alpha": 1}
    assert first["evidence"]["scores"]["hit@5"] == 1.0
    # beta ranks last behind both gap cards, which compete in a single ranking.
    assert second["evidence"]["expected"] == {"beta": 4}
    assert second["evidence"]["scores"]["hit@1"] == 0.0
    assert second["gap"] is None
    summary = retrieval.summarize_rows(rows, ("dev",), gap_channel=False)
    assert summary["dev"]["evidence"]["n"] == 2
    assert summary["dev"]["gap"] is None
