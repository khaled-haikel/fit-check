"""POST /checks and GET /checks/{id} with an in-memory store."""

import pytest
from checks_fakes import FakeStore

from fitcheck_api.checks.api import CheckDeps, check_view, get_check, post_check
from fitcheck_api.checks.ids import check_key, ip_hash, rate_key, spend_key
from fitcheck_api.checks.store import STALE_SECONDS, Limits

NOW = 1_791_892_800.0
VERSIONS = {"analysis_version": "v" * 64, "model": "us.anthropic.claude-sonnet-4-6", "index_version": "i" * 64}
JD = "Backend engineer. " + "We need Python, AWS Lambda and Terraform experience. " * 6
IP = "203.0.113.10"


class Deps:
    def __init__(self, **overrides):
        self.store = FakeStore()
        self.sent: list[str] = []
        self.logs: list[tuple[str, dict]] = []
        self.clock = [NOW]
        fields = dict(
            store=self.store,
            enqueue=self.sent.append,
            limits=Limits(daily_per_ip=2, monthly_cap_micro=2_000_000, per_check_cap_micro=500_000),
            jd_min_chars=200,
            jd_max_chars=20000,
            versions=VERSIONS,
            unavailable=None,
            clock=lambda: self.clock[0],
            log=lambda message, **extra: self.logs.append((message, extra)),
        )
        fields.update(overrides)
        self.deps = CheckDeps(**fields)


@pytest.fixture
def d():
    return Deps()


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ([1, 2], "the body must be a JSON object"),
        ({}, "invalid request"),
        ({"job_description": JD, "extra": 1}, "invalid request"),
        ({"job_description": 42}, "invalid request"),
        ({"job_description": "Too short. " * 10}, "characters after normalization"),
        ({"job_description": "x" * 20001}, "characters after normalization"),
        ({"job_description": "   ".join(["short"] * 30)}, "characters after normalization"),
    ],
    ids=["not-an-object", "missing", "extra-field", "not-a-string", "too-short", "too-long", "short-after-normalization"],
)
def test_post_rejects_invalid_bodies(d, body, error):
    reply = post_check(body, IP, d.deps)

    assert reply.status == 400 and error in str(reply.body)
    assert d.store.calls == []


def test_post_creates_queues_and_reserves(d):
    reply = post_check({"job_description": JD}, IP, d.deps)

    assert reply.status == 202
    assert reply.body["status"] == "queued" and reply.body["poll_after_ms"] == 3000
    assert d.sent == [reply.body["check_id"]]
    assert d.store.spend("2026-10") == {"committed": 500_000, "reserved": 500_000, "settled": 0}
    assert d.store.items[rate_key(ip_hash(IP), "2026-10-13")]["count"] == 1
    # The raw IP and the job description are never logged.
    assert IP not in str(d.logs) and JD not in str(d.logs)


def test_the_same_job_description_is_served_from_the_cache(d):
    first = post_check({"job_description": JD}, IP, d.deps)
    again = post_check({"job_description": "  " + JD.replace(" ", "  ") + "\n\n\n"}, "198.51.100.7", d.deps)

    assert again.status == 200 and again.body == {"check_id": first.body["check_id"], "status": "queued", "cached": True}
    assert d.sent == [first.body["check_id"]]
    # A cached answer does not count against the second client's daily limit.
    assert rate_key(ip_hash("198.51.100.7"), "2026-10-13") not in d.store.items


def test_a_failed_or_stale_check_can_run_again(d):
    first = post_check({"job_description": JD}, IP, d.deps).body["check_id"]
    d.store.items[check_key(first)]["status"] = "failed"
    assert post_check({"job_description": JD}, IP, d.deps).status == 202

    d.store.items[check_key(first)]["updated_epoch"] = int(NOW) - STALE_SECONDS - 1
    assert post_check({"job_description": JD}, "198.51.100.7", d.deps).status == 202


def test_the_daily_limit_per_ip(d):
    for n in range(2):
        assert post_check({"job_description": JD + f" Variant {n}."}, IP, d.deps).status == 202

    reply = post_check({"job_description": JD + " Variant 3."}, IP, d.deps)

    assert reply.status == 429 and reply.body == {"error": "daily_limit"}
    assert post_check({"job_description": JD + " Variant 3."}, "198.51.100.7", d.deps).status == 202


@pytest.mark.parametrize(
    ("spend", "error"),
    [({"committed": 1_600_000, "reserved": 0, "settled": 1_600_000}, "monthly_budget"), ({"committed": 1_600_000, "reserved": 1_000_000, "settled": 600_000}, "busy")],
)
def test_the_monthly_budget(d, spend, error):
    d.store.items[spend_key("2026-10")] = spend

    reply = post_check({"job_description": JD}, IP, d.deps)

    assert reply.status == 429 and reply.body == {"error": error}
    assert d.sent == []


def test_an_enqueue_failure_fails_the_check_and_releases_the_reservation():
    def broken(identifier):
        raise RuntimeError("queue down")

    d = Deps(enqueue=broken)

    reply = post_check({"job_description": JD}, IP, d.deps)

    assert reply.status == 503
    checks = [item for key, item in d.store.items.items() if key.startswith("check#")]
    assert len(checks) == 1
    assert checks[0]["status"] == "failed" and checks[0]["error"] == "enqueue_failed"
    assert d.store.spend("2026-10") == {"committed": 0, "reserved": 0, "settled": 0}


def test_post_refuses_when_analysis_is_unavailable():
    d = Deps(unavailable="the packaged search index is a placeholder", versions=None)

    reply = post_check({"job_description": JD}, IP, d.deps)

    assert reply.status == 503 and d.store.calls == []


def test_get_validates_the_id_and_returns_404(d):
    assert get_check("NOT-A-VALID-ID", d.deps).status == 400
    assert get_check("a" * 25, d.deps).status == 400
    assert get_check("a" * 26, d.deps).status == 404


def _stored(d, **fields):
    identifier = post_check({"job_description": JD}, IP, d.deps).body["check_id"]
    d.store.items[check_key(identifier)].update(fields)
    return identifier


REQUIREMENTS = [
    {"id": "r01", "source": "Python", "text_en": "Python.", "type": "must", "category": "skill"},
    {"id": "r02", "source": "Terraform", "text_en": "Terraform.", "type": "nice", "category": "skill"},
]


def test_get_shows_requirements_before_the_verdicts(d):
    identifier = _stored(d, status="judging", requirements=REQUIREMENTS)

    body = get_check(identifier, d.deps).body

    assert body["status"] == "judging"
    assert [r["id"] for r in body["requirements"]] == ["r01", "r02"]
    assert body["requirements"][0]["verdict"] is None and body["score"] is None
    assert "job_description" not in body and JD not in str(body)


def test_get_returns_the_result_when_done(d):
    citations = [{"card_id": "card-a", "title": "Card A", "quote": "Built Python services.", "proof": [{"type": "repo", "url": "https://example.com"}]}]
    judgments = [
        {"requirement_id": "r01", "verdict": "meets", "reason": "Shown.", "downgraded": False, "citations": citations},
        {"requirement_id": "r02", "verdict": "does_not_meet", "reason": "Not shown.", "downgraded": True, "citations": []},
    ]
    score = {"value": 67, "must": {"meets": 1, "partial": 0, "does_not_meet": 0}, "nice": {"meets": 0, "partial": 0, "does_not_meet": 1}}
    identifier = _stored(d, status="done", requirements=REQUIREMENTS, judgments=judgments, score=score, cost_micro=123_456, latency_ms=41000)

    body = get_check(identifier, d.deps).body

    assert body["status"] == "done" and body["score"] == score and body["error"] is None
    assert body["requirements"][0]["citations"] == citations and body["requirements"][1]["downgraded"] is True
    assert body["usage"] == {"cost_usd": 0.123456, "latency_ms": 41000}
    assert set(body) == {"check_id", "status", "created_at", "updated_at", "analysis_version", "model", "index_version", "requirements", "score", "usage", "error"}


def test_a_stale_check_reads_as_timed_out_without_a_write(d):
    identifier = _stored(d, status="extracting")
    d.clock[0] = NOW + STALE_SECONDS + 60
    calls_before = list(d.store.calls)

    body = get_check(identifier, d.deps).body

    assert body["status"] == "failed" and body["error"] == "timed_out"
    assert d.store.items[check_key(identifier)]["status"] == "extracting"
    assert d.store.calls == calls_before + ["get"]


def test_check_view_of_a_failed_check():
    item = {"check_id": "x" * 26, "status": "failed", "error": "cost_cap", "created_at": "a", "updated_at": "b",
            "analysis_version": "v", "model": "m", "index_version": "i", "updated_epoch": int(NOW)}

    view = check_view(item, NOW)

    assert view["status"] == "failed" and view["error"] == "cost_cap" and view["requirements"] == []
