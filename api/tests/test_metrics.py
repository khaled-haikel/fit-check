import pytest

from fitcheck_api.metrics import percentile, recall_at_k, reciprocal_rank, regressions, score_requirement, summarize

RANKING = ["a", "b", "c", "d", "e", "f"]


def test_recall_at_k():
    assert recall_at_k(RANKING, {"a"}, 1) == 1.0
    assert recall_at_k(RANKING, {"a", "c"}, 1) == 0.5
    assert recall_at_k(RANKING, {"a", "c"}, 3) == 1.0
    assert recall_at_k(RANKING, {"f"}, 5) == 0.0
    assert recall_at_k(RANKING, {"a", "e", "f"}, 5) == pytest.approx(2 / 3)


def test_recall_needs_relevant_cards():
    with pytest.raises(ValueError):
        recall_at_k(RANKING, set(), 5)


def test_reciprocal_rank_uses_the_first_relevant_card():
    assert reciprocal_rank(RANKING, {"a"}) == 1.0
    assert reciprocal_rank(RANKING, {"d", "b"}) == 0.5
    assert reciprocal_rank(RANKING, {"f"}) == pytest.approx(1 / 6)
    assert reciprocal_rank(RANKING, {"z"}) == 0.0


def test_score_requirement_reports_every_metric():
    assert score_requirement(RANKING, {"c", "f"}) == {
        "recall@1": 0.0,
        "recall@3": 0.5,
        "recall@5": 0.5,
        "mrr": pytest.approx(1 / 3),
    }


def test_summarize_macro_averages():
    rows = [score_requirement(RANKING, {"a"}), score_requirement(RANKING, {"f"})]

    summary = summarize(rows)

    assert summary["n"] == 2
    assert summary["recall@1"] == 0.5
    assert summary["recall@5"] == 0.5
    assert summary["mrr"] == pytest.approx((1 + 1 / 6) / 2)


def test_summarize_of_nothing():
    assert summarize([]) == {"n": 0}


def test_percentile_nearest_rank():
    values = [5, 1, 4, 2, 3, 10, 9, 8, 7, 6]

    assert percentile(values, 50) == 5
    assert percentile(values, 95) == 10
    assert percentile(values, 100) == 10
    assert percentile([], 50) == 0.0


BASELINE = {"dev": {"recall@5": 0.80}, "test": {"recall@5": 0.70}}


def test_no_regression_within_the_tolerance():
    current = {"dev": {"recall@5": 0.78}, "test": {"recall@5": 0.75}}

    assert regressions(current, BASELINE) == []


def test_a_drop_beyond_the_tolerance_is_a_regression():
    current = {"dev": {"recall@5": 0.779}, "test": {"recall@5": 0.70}}

    problems = regressions(current, BASELINE)

    assert len(problems) == 1
    assert problems[0].startswith("dev:")


def test_a_split_missing_from_the_run_is_a_regression():
    assert regressions({"dev": {"recall@5": 0.9}}, BASELINE) == ["test: missing from the current run"]
