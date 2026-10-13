"""Lambda entry point for the fit-check HTTP API (API Gateway HTTP API, payload v2)."""

import os

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
app = APIGatewayHttpResolver()


@app.get("/health")
def health() -> dict:
    # GIT_COMMIT is set per deploy, so each published version reports the
    # commit it was built from.
    commit = os.environ.get("GIT_COMMIT", "unknown")
    logger.info("Health check", extra={"commit": commit})
    return {"ok": True, "commit": commit}


# The correlation ID is API Gateway's request ID, which it also returns to the
# caller in the apigw-requestid response header.
@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
def handler(event: dict, context: LambdaContext) -> dict:
    return app.resolve(event, context)
