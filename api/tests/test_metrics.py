import pytest

from fitcheck_api.metrics import hit_at_k, percentile, recall_at_k, reciprocal_rank, regressions, score_ranking, summarize

RANKING = ["a", "b", "c", "d", "e", "f"]


def test_hit_at_k():
    assert hit_at_k(RANKING, {"a"}, 1) == 1.0
    assert hit_at_k(RANKING, {"c", "z"}, 1) == 0.0
    assert hit_at_k(RANKING, {"c", "z"}, 3) == 1.0
    assert hit_at_k(RANKING, {"f"}, 5) == 0.0


def test_recall_at_k():
    assert recall_at_k(RANKING, {"a"}, 1) == 1.0
    assert recall_at_k(RANKING, {"a", "c"}, 1) == 0.5
    assert recall_at_k(RANKING, {"a", "c"}, 3) == 1.0
    assert recall_at_k(RANKING, {"f"}, 5) == 0.0
    assert recall_at_k(RANKING, {"a", "e", "f"}, 5) == pytest.approx(2 / 3)


def test_a_card_missing_from_the_ranking_counts_as_not_found():
    assert recall_at_k(["a", "b"], {"a", "excluded"}, 5) == 0.5
    assert hit_at_k(["a", "b"], {"excluded"}, 5) == 0.0
    assert reciprocal_rank(["a", "b"], {"excluded"}) == 0.0


@pytest.mark.parametrize("metric", [hit_at_k, recall_at_k])
def test_metrics_need_expected_cards(metric):
    with pytest.raises(ValueError):
        metric(RANKING, set(), 5)


def test_reciprocal_rank_uses_the_first_expected_card():
    assert reciprocal_rank(RANKING, {"a"}) == 1.0
    assert reciprocal_rank(RANKING, {"d", "b"}) == 0.5
    assert reciprocal_rank(RANKING, {"f"}) == pytest.approx(1 / 6)
    assert reciprocal_rank(RANKING, {"z"}) == 0.0


def test_score_ranking_reports_every_metric():
    assert score_ranking(RANKING, {"c", "f"}) == {
        "hit@1": 0.0,
        "recall@1": 0.0,
        "hit@3": 1.0,
        "recall@3": 0.5,
        "hit@5": 1.0,
        "recall@5": 0.5,
        "mrr": pytest.approx(1 / 3),
    }


def test_summarize_macro_averages():
    rows = [score_ranking(RANKING, {"a"}), score_ranking(RANKING, {"f"})]

    summary = summarize(rows)

    assert summary["n"] == 2
    assert summary["hit@1"] == 0.5
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


BASELINE = {"dev": {"hit@5": 0.90, "recall@5": 0.80}, "test": {"hit@5": 0.85, "recall@5": 0.70}}


def test_no_regression_within_the_tolerance():
    current = {"dev": {"hit@5": 0.88, "recall@5": 0.78}, "test": {"hit@5": 0.90, "recall@5": 0.75}}

    assert regressions(current, BASELINE) == []


def test_a_drop_beyond_the_tolerance_in_either_metric_is_a_regression():
    current = {"dev": {"hit@5": 0.90, "recall@5": 0.779}, "test": {"hit@5": 0.829, "recall@5": 0.70}}

    problems = regressions(current, BASELINE)

    assert len(problems) == 2
    assert problems[0].startswith("dev: recall@5")
    assert problems[1].startswith("test: hit@5")


def test_a_split_missing_from_the_run_is_a_regression():
    current = {"dev": {"hit@5": 0.9, "recall@5": 0.9}}

    assert regressions(current, BASELINE) == ["test: missing from the current run"]
