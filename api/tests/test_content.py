"""Schema and cross-check tests with their own fixtures, never the real cards or cases."""

from pathlib import Path

import pytest
import yaml

from fitcheck_api.content import card_records, load_content


def _card(card_id: str = "sample-project", **overrides) -> dict:
    card = {
        "id": card_id,
        "kind": "project",
        "title": "Sample project",
        "summary": "A sample project used by the tests.",
        "claims": ["Built the sample."],
        "skills": ["python"],
        "proof": [{"type": "repo", "url": "https://example.com/sample"}],
    }
    card.update(overrides)
    return card


def _requirement(requirement_id: str = "r01", **overrides) -> dict:
    requirement = {
        "id": requirement_id,
        "text": "Python experience",
        "text_en": "Python experience",
        "type": "must",
        "category": "skill",
        "verdict": "meets",
        "evidence": ["sample-project"],
    }
    requirement.update(overrides)
    return requirement


def _case(case_id: str = "dev-01-sample", **overrides) -> dict:
    case = {
        "id": case_id,
        "split": "dev",
        "language": "en",
        "source": "synthetic",
        "collected": "2026-01-01",
        "role_title": "Sample engineer",
        "job_description": "A sample job description.",
        "requirements": [_requirement()],
    }
    case.update(overrides)
    return case


class Repo:
    def __init__(self, root: Path) -> None:
        self.evidence = root / "evidence"
        self.cases = root / "cases"
        self.evidence.mkdir()
        for split in ("dev", "test", "special"):
            (self.cases / split).mkdir(parents=True)

    def card(self, data: dict, name: str | None = None) -> None:
        (self.evidence / f"{name or data['id']}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    def case(self, data: dict, name: str | None = None, folder: str | None = None) -> None:
        path = self.cases / (folder or data["split"]) / f"{name or data['id']}.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")

    def errors(self) -> list[str]:
        return load_content(self.evidence, self.cases).errors


@pytest.fixture
def repo(tmp_path) -> Repo:
    repo = Repo(tmp_path)
    repo.card(_card())
    repo.card(_card("missing-skill", kind="gap", proof=[{"type": "none", "note": "Not done yet."}]))
    return repo


def _single_error(repo: Repo) -> str:
    errors = repo.errors()
    assert len(errors) == 1, errors
    return errors[0]


def test_valid_content_has_no_errors(repo):
    repo.case(_case())
    repo.case(_case("dev-02-derived", source="derived", derived_from="dev-01-sample"))
    repo.case(_case("test-01-gap", split="test", requirements=[_requirement(verdict="does_not_meet", evidence=["missing-skill"])]))
    repo.case(_case("special-01-empty", split="special", special="off_domain", requirements=[_requirement(verdict="does_not_meet", evidence=[])]))

    assert repo.errors() == []


@pytest.mark.parametrize("period", ["2024", "2024-05", "2023-01/2024-12", "2024-05/present"])
def test_valid_periods(repo, period):
    repo.card(_card("dated", period=period))

    assert repo.errors() == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"kind": "hobby"}, "kind"),
        ({"title": "   "}, "title"),
        ({"summary": ""}, "summary"),
        ({"claims": []}, "claims"),
        ({"claims": [f"Claim {n}." for n in range(9)]}, "claims"),
        ({"skills": ["Python"]}, "skills.0"),
        ({"proof": []}, "proof"),
        ({"proof": [{"type": "repo", "url": "http://example.com"}]}, "proof.0.url"),
        ({"proof": [{"type": "video"}]}, "proof.0.type"),
        ({"period": "May 2024"}, "period"),
        ({"period": "2024/present"}, "period"),
        ({"verifiable": True}, "verifiable"),
    ],
    ids=[
        "unknown-kind",
        "blank-title",
        "empty-summary",
        "no-claims",
        "nine-claims",
        "uppercase-skill",
        "no-proof",
        "http-url",
        "unknown-proof-type",
        "free-text-period",
        "year-only-range",
        "stored-verifiable",
    ],
)
def test_invalid_cards(repo, overrides, fragment):
    repo.card(_card("broken", **overrides))

    error = _single_error(repo)
    assert "broken.yaml" in error
    assert f": {fragment}" in error


def test_card_id_must_be_kebab_case(repo):
    repo.card(_card("Not_Kebab"), name="not-kebab")

    assert "id" in _single_error(repo)


def test_card_file_name_must_match_the_id(repo):
    repo.card(_card("other-name"), name="file-name")

    assert "must match the file name" in _single_error(repo)


def test_empty_and_invalid_yaml_files(repo):
    (repo.evidence / "empty.yaml").write_text("", encoding="utf-8")
    (repo.evidence / "bad.yaml").write_text("id: [unclosed", encoding="utf-8")

    errors = repo.errors()

    assert any("empty.yaml: the file is empty" in error for error in errors)
    assert any("bad.yaml: invalid YAML" in error for error in errors)


def test_case_cites_an_unknown_card(repo):
    repo.case(_case(requirements=[_requirement(evidence=["no-such-card"])]))

    assert "unknown cards ['no-such-card']" in _single_error(repo)


def test_supported_verdicts_need_evidence(repo):
    repo.case(_case(requirements=[_requirement(verdict="partial", evidence=[])]))

    assert "must cite at least one evidence card" in _single_error(repo)


def test_does_not_meet_may_cite_only_gap_cards(repo):
    repo.case(_case(requirements=[_requirement(verdict="does_not_meet", evidence=["sample-project"])]))

    assert "may cite only gap cards" in _single_error(repo)


def test_derived_case_needs_an_existing_source(repo):
    repo.case(_case("dev-02-derived", source="derived", derived_from="dev-99-missing"))

    assert "is not a known case" in _single_error(repo)


def test_derived_case_must_name_its_source(repo):
    repo.case(_case(source="derived"))

    assert "derived_from" in _single_error(repo)


def test_derived_from_only_on_derived_cases(repo):
    repo.case(_case(derived_from="dev-01-sample"))

    assert "only allowed when source is derived" in _single_error(repo)


def test_case_split_must_match_its_folder(repo):
    repo.case(_case(), folder="test")

    assert "must match the folder 'test'" in _single_error(repo)


def test_case_file_name_must_match_the_id(repo):
    repo.case(_case(), name="dev-01-other")

    assert "must match the file name" in _single_error(repo)


def test_duplicate_requirement_ids(repo):
    repo.case(_case(requirements=[_requirement(), _requirement()]))

    assert "duplicate requirement ids" in _single_error(repo)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"split": "train"}, "split"),
        ({"language": "es"}, "language"),
        ({"source": "scraped"}, "source"),
        ({"special": "jailbreak"}, "special"),
        ({"collected": "yesterday"}, "collected"),
        ({"job_description": " "}, "job_description"),
        ({"requirements": []}, "requirements"),
    ],
)
def test_invalid_case_fields(repo, overrides, fragment):
    repo.case(_case(**overrides), folder="dev")

    errors = repo.errors()
    assert any(f": {fragment}" in error for error in errors), errors


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"id": "1"}, "id"),
        ({"text_en": ""}, "text_en"),
        ({"type": "should"}, "type"),
        ({"category": "salary"}, "category"),
        ({"verdict": "maybe"}, "verdict"),
        ({"evidence": ["Not Kebab"]}, "evidence.0"),
    ],
)
def test_invalid_requirements(repo, overrides, fragment):
    repo.case(_case(requirements=[_requirement(**overrides)]))

    assert f"requirements.0.{fragment}" in _single_error(repo)


def test_card_records_are_sorted_and_leave_out_unset_fields(repo):
    repo.card(_card("aaa-first"))

    records = card_records(load_content(repo.evidence))

    assert [record["id"] for record in records] == ["aaa-first", "missing-skill", "sample-project"]
    assert "period" not in records[0]
    assert "verifiable" not in records[0]
