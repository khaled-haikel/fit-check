"""Calls to Claude through Bedrock InvokeModel, in the Messages format (ADR 0004).

No structured output, tools, sampling parameters or prefill: the prompts ask
for a JSON object in the text, and the code validates it. The client is
injected (a boto3 bedrock-runtime client, or the evaluation's caching wrapper).
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .costs import CallGuard, CallRecord, ModelPrice, guarded_call, usage_cost, worst_case_cost
from .errors import MODEL_REFUSED, AnalysisError
from .jsonout import last_text_block

ANTHROPIC_VERSION = "bedrock-2023-05-31"
# The judgment can take more than botocore's default 60 s; a timeout there
# would retry the call and pay for it twice.
READ_TIMEOUT_SECONDS = 120


def bedrock_client(for_ci: bool = False) -> Any:
    """The Lambda gets one retry in standard mode; CI's long runs get adaptive retries."""
    import boto3
    from botocore.config import Config

    retries = {"mode": "adaptive", "max_attempts": 5} if for_ci else {"mode": "standard", "max_attempts": 2}
    return boto3.client("bedrock-runtime", config=Config(read_timeout=READ_TIMEOUT_SECONDS, retries=retries))


@dataclass(frozen=True)
class CallSettings:
    thinking: bool
    effort: str
    max_tokens: int

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> CallSettings:
        return cls(thinking=bool(config["thinking"]), effort=str(config["effort"]), max_tokens=int(config["max_tokens"]))


@dataclass(frozen=True)
class ClaudeReply:
    # The last text block, or None if the response had none.
    text: str | None
    stop_reason: str | None
    record: CallRecord


def request_body(system: str, user_blocks: Sequence[str], settings: CallSettings) -> str:
    """The InvokeModel body: the system prompt marked for prompt caching, one user turn."""
    body: dict[str, Any] = {
        "anthropic_version": ANTHROPIC_VERSION,
        "max_tokens": settings.max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": block} for block in user_blocks]}],
        "output_config": {"effort": settings.effort},
    }
    # Omitting the field leaves thinking off on Sonnet 4.6; the 5.x models
    # think by default and reject "disabled", so only these two forms are used.
    if settings.thinking:
        body["thinking"] = {"type": "adaptive"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def call_claude(
    client: Any,
    model_id: str,
    price: ModelPrice,
    system: str,
    user_blocks: Sequence[str],
    settings: CallSettings,
    phase: str,
    attempt: int,
    guards: Sequence[CallGuard],
) -> ClaudeReply:
    body = request_body(system, user_blocks, settings)
    worst = worst_case_cost(price, len(system) + sum(len(block) for block in user_blocks), settings.max_tokens)
    return _invoke(client, model_id, body, worst, phase, attempt, guards, price)


def _invoke(
    client: Any,
    model_id: str,
    body: str,
    worst: Decimal,
    phase: str,
    attempt: int,
    guards: Sequence[CallGuard],
    price: ModelPrice,
) -> ClaudeReply:
    is_cached = getattr(client, "is_cached", None)
    cached = bool(is_cached(model_id, body)) if is_cached else False

    def call() -> tuple[CallRecord, dict[str, Any]]:
        started = time.perf_counter()
        response = client.invoke_model(modelId=model_id, body=body, contentType="application/json", accept="application/json")
        payload = json.loads(response["body"].read())
        elapsed_ms = (time.perf_counter() - started) * 1000
        meta = response.get("ResponseMetadata", {})
        usage = payload.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        record = CallRecord(
            phase=phase,
            model=model_id,
            attempt=attempt,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_write_tokens=cache_write,
            cache_read_tokens=cache_read,
            # A cached response keeps the latency of the call that produced it.
            latency_ms=float(meta.get("FitcheckLatencyMs", elapsed_ms)),
            stop_reason=payload.get("stop_reason"),
            request_id=str(meta.get("RequestId", "")),
            retry_attempts=int(meta.get("RetryAttempts", 0)),
            cached=bool(meta.get("FitcheckCached", False)),
            cost_usd=usage_cost(price, input_tokens, output_tokens, cache_write, cache_read),
        )
        return record, payload

    record, payload = guarded_call(guards, phase, worst, cached, call, lambda result: result[0])
    if record.stop_reason == "refusal":
        raise AnalysisError(MODEL_REFUSED, phase)
    try:
        text = last_text_block(payload.get("content") or [])
    except ValueError:
        text = None
    return ClaudeReply(text=text, stop_reason=record.stop_reason, record=record)
