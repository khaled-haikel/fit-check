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
