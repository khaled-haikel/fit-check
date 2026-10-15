import json

from fitcheck_api.handler import handler


def test_health_returns_ok_and_the_deployed_commit(monkeypatch, http_event, lambda_context):
    monkeypatch.setenv("GIT_COMMIT", "0123456789abcdef0123456789abcdef01234567")

    response = handler(http_event("/health"), lambda_context)

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "application/json"
    assert json.loads(response["body"]) == {
        "ok": True,
        "commit": "0123456789abcdef0123456789abcdef01234567",
    }


def test_health_reports_unknown_commit_when_not_set(monkeypatch, http_event, lambda_context):
    monkeypatch.delenv("GIT_COMMIT", raising=False)

    response = handler(http_event("/health"), lambda_context)

    assert json.loads(response["body"]) == {"ok": True, "commit": "unknown"}


def test_logs_are_json_with_the_request_correlation_id(monkeypatch, http_event, lambda_context, log_stream):
    monkeypatch.setenv("GIT_COMMIT", "0123456789abcdef0123456789abcdef01234567")

    handler(http_event("/health", request_id="req-42"), lambda_context)

    records = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    health_logs = [record for record in records if record["message"] == "Health check"]
    assert len(health_logs) == 1
    assert health_logs[0]["correlation_id"] == "req-42"
    assert health_logs[0]["commit"] == "0123456789abcdef0123456789abcdef01234567"
    assert health_logs[0]["function_request_id"] == lambda_context.aws_request_id


def test_unknown_route_returns_404(http_event, lambda_context):
    response = handler(http_event("/nope"), lambda_context)

    assert response["statusCode"] == 404


def test_a_queue_message_goes_to_the_worker_with_the_check_id_as_correlation_id(monkeypatch, lambda_context, log_stream):
    from fitcheck_api import handler as handler_module

    seen = []

    def fake_process(identifier, deps, remaining_ms):
        handler_module.logger.info("Processing")
        seen.append(identifier)
        return "done"

    monkeypatch.setattr(handler_module, "process", fake_process)
    monkeypatch.setattr(handler_module, "worker_deps", lambda: None)
    lambda_context.get_remaining_time_in_millis = lambda: 290_000
    event = {"Records": [{"eventSource": "aws:sqs", "body": json.dumps({"check_id": "b" * 26}), "messageId": "m1"}]}

    assert handler(event, lambda_context) == {}
    assert seen == ["b" * 26]
    records = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    assert [r["correlation_id"] for r in records if r["message"] == "Processing"] == ["b" * 26]


def test_post_and_get_checks_through_the_handler(monkeypatch, http_event, lambda_context):
    from checks_fakes import FakeStore

    from fitcheck_api import handler as handler_module
    from fitcheck_api.checks.api import CheckDeps
    from fitcheck_api.checks.store import Limits

    store, sent = FakeStore(), []
    deps = CheckDeps(
        store=store,
        enqueue=sent.append,
        limits=Limits(5, 2_000_000, 500_000),
        jd_min_chars=200,
        jd_max_chars=20000,
        versions={"analysis_version": "v", "model": "m", "index_version": "i"},
        unavailable=None,
        clock=lambda: 1_791_892_800.0,
        log=lambda message, **extra: None,
    )
    monkeypatch.setattr(handler_module, "checks_deps", lambda: deps)
    monkeypatch.setenv("CHECKS_TABLE", "fitcheck-checks")
    body = json.dumps({"job_description": "We need Python and AWS experience for a backend role. " * 5})

    created = handler(http_event("/checks", method="POST", body=body), lambda_context)
    identifier = json.loads(created["body"])["check_id"]
    fetched = handler(http_event(f"/checks/{identifier}"), lambda_context)
    missing = handler(http_event("/checks/" + "c" * 26), lambda_context)
    invalid = handler(http_event("/checks", method="POST", body="not json"), lambda_context)

    assert created["statusCode"] == 202 and sent == [identifier]
    assert fetched["statusCode"] == 200 and json.loads(fetched["body"])["status"] == "queued"
    assert missing["statusCode"] == 404
    assert invalid["statusCode"] == 400
