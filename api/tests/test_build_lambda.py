"""The packaging script must refuse an index a deploy should never ship."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_lambda.py"


@pytest.fixture(scope="module")
def build_lambda():
    spec = importlib.util.spec_from_file_location("build_lambda", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, index: dict) -> Path:
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    return path


def _real_index(build_lambda) -> dict:
    config = build_lambda.embedding_config()
    dimensions = int(config["dimensions"])
    return {
        "index_version": "abc",
        "model": config["model_id"],
        "dimensions": dimensions,
        "placeholder": False,
        "retrieval": build_lambda.retrieval_config(),
        "cards": [{"id": "a"}],
        "chunks": [{"card": "a", "kind": "card", "text": "a"}, {"card": "a", "kind": "claim", "text": "a"}],
        "embedded": ["card"],
        "vectors": [[0.0] * dimensions],
    }


def test_a_real_index_is_accepted(build_lambda, tmp_path):
    index = _real_index(build_lambda)

    assert build_lambda.checked_index(_write(tmp_path, index)) == index


def test_the_placeholder_is_refused(build_lambda, tmp_path):
    with pytest.raises(SystemExit, match="it is a placeholder"):
        build_lambda.checked_index(_write(tmp_path, build_lambda.placeholder_index()))


def test_an_index_from_another_model_is_refused(build_lambda, tmp_path):
    index = _real_index(build_lambda) | {"model": "another-model"}

    with pytest.raises(SystemExit, match="config/models.json says"):
        build_lambda.checked_index(_write(tmp_path, index))


def test_an_index_with_missing_vectors_is_refused(build_lambda, tmp_path):
    index = _real_index(build_lambda) | {"vectors": []}

    with pytest.raises(SystemExit, match="1 cards, 1 embedded chunks and 0 vectors"):
        build_lambda.checked_index(_write(tmp_path, index))


def test_an_index_with_other_retrieval_settings_is_refused(build_lambda, tmp_path):
    index = _real_index(build_lambda) | {"retrieval": {"chunks": "something-else"}}

    with pytest.raises(SystemExit, match="differ from config/retrieval.json"):
        build_lambda.checked_index(_write(tmp_path, index))


def test_the_placeholder_matches_the_configured_model(build_lambda):
    placeholder = build_lambda.placeholder_index()
    config = build_lambda.embedding_config()

    assert placeholder["placeholder"] is True
    assert placeholder["model"] == config["model_id"]
    assert placeholder["cards"] == [] and placeholder["chunks"] == [] and placeholder["vectors"] == []
    assert placeholder["embedded"] == []
    assert placeholder["retrieval"] == build_lambda.retrieval_config()
