import io
from dataclasses import dataclass

import pytest

from fitcheck_api import handler as handler_module


@dataclass
class FakeLambdaContext:
    function_name: str = "fitcheck-api"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:000000000000:function:fitcheck-api:live"
    aws_request_id: str = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def lambda_context() -> FakeLambdaContext:
    return FakeLambdaContext()


@pytest.fixture
def http_event():
    """Builds an API Gateway HTTP API (payload format 2.0) event."""

    def make(
        path: str = "/health", method: str = "GET", request_id: str = "test-request-id", body: str | None = None
    ) -> dict:
        event = {
            "version": "2.0",
            "routeKey": f"{method} {path}",
            "rawPath": path,
            "rawQueryString": "",
            "headers": {"host": "example.execute-api.us-east-1.amazonaws.com"},
            "requestContext": {
                "accountId": "000000000000",
                "apiId": "example",
                "domainName": "example.execute-api.us-east-1.amazonaws.com",
                "http": {
                    "method": method,
                    "path": path,
                    "protocol": "HTTP/1.1",
                    "sourceIp": "203.0.113.10",
                    "userAgent": "pytest",
                },
                "requestId": request_id,
                "routeKey": f"{method} {path}",
                "stage": "$default",
                "time": "13/Oct/2026:12:00:00 +0000",
                "timeEpoch": 1791892800000,
            },
            "isBase64Encoded": False,
        }
        if body is not None:
            event["body"] = body
            event["headers"]["content-type"] = "application/json"
        return event

    return make


@pytest.fixture
def log_stream(monkeypatch) -> io.StringIO:
    """Captures the JSON lines written by the module-level Powertools logger."""
    stream = io.StringIO()
    monkeypatch.setattr(handler_module.logger.registered_handler, "stream", stream)
    return stream
