"""The queue worker: lease, phases with resume, deadline, settlement, expected and unexpected errors."""

import json
from pathlib import Path

import pytest
from bedrock_fakes import FakeBedrock, claude_payload
from botocore.exceptions import ClientError
from checks_fakes import FakeStore
from indexes import make_index

from fitcheck_api.analysis.assets import load_assets
from fitcheck_api.analysis.normalize import normalize_job_description
from fitcheck_api.checks.ids import check_key, iso
from fitcheck_api.checks.store import Limits, new_check_item
from fitcheck_api.checks.worker import DeadlineGuard, WorkerDeps, process
from fitcheck_api.embeddings import Embedder

ROOT = Path(__file__).resolve().parents[2]
NOW = 1_791_892_800.0
JD = normalize_job_description(
    "Platform Engineer\nRemote within Latin America.\n\nRequirements\n"
    "- 3+ years of professional software engineering.\n- Hands-on Terraform with remote state.\n- Production Kubernetes experience.\n"
)
ID = "a" * 26
LIMITS = Limits(daily_per_ip=5, monthly_cap_micro=2_000_000, per_check_cap_micro=500_000)
VERSIONS = {"analysis_version": "v", "model": "us.anthropic.claude-sonnet-4-6", "index_version": "test-version"}


def _card(card_id, kind, claims):
    return {"id": card_id, "kind": kind, "title": f"Title of {card_id}", "summary": "A short summary for tests.", "claims": claims,
            "skills": ["x"], "proof": [{"type": "repo", "url": "https://example.com"}]}


CARDS = [
    _card("proj-terraform", "project", ["Built Terraform modules with S3 remote state and locking."]),
    _card("profile-years", "profile", ["About three years of professional software engineering."]),
    _card("gap-k8s", "gap", ["No production Kubernetes experience."]),
]
VECTORS = {"proj-terraform": [1, 0, 0], "profile-years": [0, 1, 0], "gap-k8s": [0, 0, 1]}
TEXT_VECTORS = {"Hands-on Terraform with remote state.": [1, 0, 0], "3+ years of professional software engineering.": [0, 1, 0], "Production Kubernetes experience.": [0, 0, 1]}
EXTRACTION = claude_payload(json.dumps({"requirements": [
    {"source": "3+ years of professional software engineering", "text_en": "3+ years of professional software engineering.", "type": "must", "category": "experience"},
    {"source": "Hands-on Terraform with remote state", "text_en": "Hands-on Terraform with remote state.", "type": "must", "category": "skill"},
    {"source": "Production Kubernetes experience", "text_en": "Production Kubernetes experience.", "type": "nice", "category": "skill"},
]}))
JUDGMENT = claude_payload(json.dumps({"judgments": [
    {"requirement_id": "r01", "verdict": "meets", "citations": [{"card_id": "profile-years", "quote": "About three years of professional software engineering"}], "reason": "Three years."},
    {"requirement_id": "r02", "verdict": "meets", "citations": [{"card_id": "proj-terraform", "quote": "Terraform modules with S3 remote state"}], "reason": "Remote state."},
    {"requirement_id": "r03", "verdict": "does_not_meet", "citations": [{"card_id": "gap-k8s", "quote": "No production Kubernetes experience"}], "reason": "A stated gap."},
]}), thinking=True)


@pytest.fixture(scope="module")
def assets():
    return load_assets(ROOT)


class World:
    def __init__(self, assets, replies, **overrides):
        self.store = FakeStore()
        self.bedrock = FakeBedrock(replies, vectors=lambda text: TEXT_VECTORS[text])
        self.logs = []
        self.store.create(new_check_item(ID, JD, VERSIONS, NOW, iso(NOW)), "h", "2026-10-13", LIMITS, NOW)
        index = make_index(CARDS, [(c["id"], "card", VECTORS[c["id"]]) for c in CARDS], retrieval={"chunks": "card", "hybrid": True, "routing": True})
        fields = dict(
            store=self.store,
            assets=assets,
            index=index,
            claude=self.bedrock,
            embedder=Embedder(assets.embedding_model, 3, client=self.bedrock),
            per_check_cap_micro=500_000,
            clock=lambda: NOW + 30,
            log=lambda message, **extra: self.logs.append((message, extra)),
        )
        fields.update(overrides)
        self.deps = WorkerDeps(**fields)

    @property
    def item(self):
        return self.store.items[check_key(ID)]

    @property
    def spend(self):
        return self.store.spend("2026-10")


PLENTY = lambda: 280_000  # noqa: E731


def test_a_full_run_goes_through_every_phase_and_settles(assets):
    world = World(assets, [EXTRACTION, JUDGMENT])

    assert process(ID, world.deps, PLENTY) == "done"

    item = world.item
    assert item["status"] == "done"
    assert [c for c in world.store.calls if c.startswith("advance")] == [
        "advance:queued->extracting", "advance:extracting->retrieving", "advance:retrieving->judging", "advance:judging->done"
    ]
    assert [j["verdict"] for j in item["judgments"]] == ["meets", "meets", "does_not_meet"]
    assert item["judgments"][1]["citations"][0] == {"card_id": "proj-terraform", "title": "Title of proj-terraform",
                                                    "quote": "Terraform modules with S3 remote state", "proof": [{"type": "repo", "url": "https://example.com"}]}
    assert item["score"]["value"] == 80
    assert isinstance(item["cost_micro"], int) and item["cost_micro"] > 0 and item["latency_ms"] == 30000
    assert item["candidates"][2]["gap"] == "gap-k8s"
    assert world.spend == {"committed": item["cost_micro"], "reserved": 0, "settled": item["cost_micro"]}
    assert sum(1 for message, _ in world.logs if message == "Model call") == 5
    assert JD not in str(world.logs)


def test_a_duplicate_delivery_does_nothing(assets):
    world = World(assets, [EXTRACTION, JUDGMENT])
    world.item["lease_until"] = int(NOW) + 200

    assert process(ID, world.deps, PLENTY) == "skipped"
    assert world.bedrock.calls == [] and "settle" not in world.store.calls


def test_a_finished_check_is_not_run_again(assets):
    world = World(assets, [EXTRACTION, JUDGMENT])
    process(ID, world.deps, PLENTY)
    calls = len(world.bedrock.calls)

    assert process(ID, world.deps, PLENTY) == "skipped"
    assert len(world.bedrock.calls) == calls and world.store.calls.count("settle") == 1


def test_a_redelivery_resumes_after_the_last_saved_phase(assets):
    world = World(assets, [JUDGMENT])
    world.item.update(
        status="retrieving",
        cost_micro=40_000,
        requirements=[
            {"id": "r01", "source": "3+ years of professional software engineering", "text_en": "3+ years of professional software engineering.", "type": "must", "category": "experience"},
            {"id": "r02", "source": "Hands-on Terraform with remote state", "text_en": "Hands-on Terraform with remote state.", "type": "must", "category": "skill"},
            {"id": "r03", "source": "Production Kubernetes experience", "text_en": "Production Kubernetes experience.", "type": "nice", "category": "skill"},
        ],
    )

    assert process(ID, world.deps, PLENTY) == "done"
    assert len(world.bedrock.claude_calls()) == 1
    assert world.item["cost_micro"] > 40_000


def test_the_deadline_fails_the_analysis_before_a_call(assets):
    world = World(assets, [EXTRACTION, JUDGMENT])

    assert process(ID, world.deps, lambda: 100_000) == "failed"
    assert world.item["status"] == "failed" and world.item["error"] == "deadline"
    assert world.bedrock.calls == []
    assert world.spend == {"committed": 0, "reserved": 0, "settled": 0}


def test_the_deadline_guard_needs_the_read_timeout_plus_15_seconds():
    guard = DeadlineGuard(lambda: 135_000)
    guard.before_call("judge", 0, False)
    with pytest.raises(Exception, match="deadline"):
        DeadlineGuard(lambda: 134_999).before_call("judge", 0, False)


def test_the_per_analysis_cap_fails_the_analysis(assets):
    world = World(assets, [EXTRACTION, JUDGMENT])
    world.item["cost_micro"] = 490_000

    assert process(ID, world.deps, PLENTY) == "failed"
    assert world.item["error"] == "cost_cap" and world.bedrock.calls == []
    assert world.spend["reserved"] == 0 and world.spend["settled"] == 490_000


def test_a_bedrock_error_is_expected(assets):
    class Denied(FakeBedrock):
        def invoke_model(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "InvokeModel")

    world = World(assets, [])
    world.deps.claude = Denied()

    assert process(ID, world.deps, PLENTY) == "failed"
    assert world.item["error"] == "bedrock_error" and "settle" in world.store.calls


def test_an_invalid_model_reply_is_expected(assets):
    world = World(assets, [claude_payload("not json"), claude_payload("still not json")])

    assert process(ID, world.deps, PLENTY) == "failed"
    assert world.item["error"] == "invalid_model_output"


def test_an_unexpected_error_propagates_without_settling(assets):
    world = World(assets, [EXTRACTION, JUDGMENT])

    def broken(*args, **kwargs):
        raise RuntimeError("DynamoDB is down")

    world.store.advance = broken

    with pytest.raises(RuntimeError):
        process(ID, world.deps, PLENTY)
    assert "settle" not in world.store.calls and world.spend["reserved"] == 500_000


def test_an_unavailable_package_fails_the_analysis(assets):
    world = World(assets, [], unavailable="the packaged prompts name another generation model")

    assert process(ID, world.deps, PLENTY) == "failed"
    assert world.item["error"] == "unavailable" and world.bedrock.calls == []
