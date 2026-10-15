"""The prompts, the few-shot file and analysis_version."""

import json
from pathlib import Path

import pytest
import yaml

from fitcheck_api.analysis.assets import EXAMPLES_PLACEHOLDER, load_assets
from fitcheck_api.analysis.normalize import normalize_for_match
from fitcheck_api.analysis.version import PROMPT_FILES, analysis_version, analysis_version_from, code_files

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = json.loads((ROOT / "prompts" / "extract_examples.json").read_text(encoding="utf-8"))["examples"]


def _case(case_id: str) -> dict:
    matches = list((ROOT / "evals" / "cases").glob(f"*/{case_id}.yaml"))
    assert len(matches) == 1, case_id
    return yaml.safe_load(matches[0].read_text(encoding="utf-8"))


def test_the_few_shot_cases_are_dev_05_and_dev_08():
    assert [example["case"] for example in EXAMPLES] == ["dev-05-applied-ai-fintech", "dev-08-cloud-platform-k8s"]
    assert all(_case(example["case"])["split"] == "dev" for example in EXAMPLES)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: example["case"])
def test_the_few_shot_file_matches_its_case(example):
    case = _case(example["case"])

    assert example["job_description"] == case["job_description"]
    labeled = [(r["text_en"], r["type"], r["category"]) for r in case["requirements"]]
    written = [(r["text_en"], r["type"], r["category"]) for r in example["requirements"]]
    assert written == labeled


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: example["case"])
def test_every_few_shot_source_is_in_the_job_description(example):
    job = normalize_for_match(example["job_description"])

    missing = [r["source"] for r in example["requirements"] if normalize_for_match(r["source"]) not in job]
    assert missing == []


def test_the_prompts_load_with_the_examples_rendered():
    assets = load_assets(ROOT)

    assert EXAMPLES_PLACEHOLDER not in assets.extract_system
    assert assets.extract_system.count("<example>") == 2
    assert "<job_description>" in assets.extract_system and "<cards>" in assets.judge_system
    assert assets.generation_model == "us.anthropic.claude-sonnet-4-6"
    assert assets.analysis["judge"]["max_tokens"] == 12000


def test_no_prompt_uses_the_test_split():
    test_ids = [path.stem for path in (ROOT / "evals" / "cases" / "test").glob("*.yaml")]
    text = "".join((ROOT / "prompts" / name).read_text(encoding="utf-8") for name in PROMPT_FILES)

    assert test_ids and not [case_id for case_id in test_ids if case_id in text]


def _inputs():
    prompts = {name: (ROOT / "prompts" / name).read_bytes() for name in PROMPT_FILES}
    return prompts, (ROOT / "config" / "analysis.json").read_bytes(), "model-a", "index-a", code_files()


def test_analysis_version_is_stable_and_ignores_line_endings():
    prompts, config, model, index, code = _inputs()
    version = analysis_version(prompts, config, model, index, code)

    assert version == analysis_version(prompts, config, model, index, code)
    assert version == analysis_version_from(ROOT, model, index)
    crlf = {name: data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n") for name, data in prompts.items()}
    assert version == analysis_version(crlf, config, model, index, code)


@pytest.mark.parametrize("change", ["prompt", "config", "model", "index", "code"])
def test_analysis_version_moves_with_each_input(change):
    prompts, config, model, index, code = _inputs()
    before = analysis_version(prompts, config, model, index, code)
    if change == "prompt":
        prompts = {**prompts, "judge.md": prompts["judge.md"] + b"\nOne more rule."}
    elif change == "config":
        config = config.replace(b'"max_tokens": 12000', b'"max_tokens": 12001')
    elif change == "model":
        model = "model-b"
    elif change == "index":
        index = "index-b"
    else:
        code = {**code, "judge.py": code["judge.py"] + b"\n# changed\n"}

    assert analysis_version(prompts, config, model, index, code) != before
