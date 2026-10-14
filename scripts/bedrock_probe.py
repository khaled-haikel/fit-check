"""Read-only Bedrock probe for fit-check, stage 2.

Run in AWS CloudShell through scripts/bedrock_probe.sh, with the console
session's credentials. It reports model availability and inference profiles,
checks Titan Text Embeddings V2, and records how the Claude generation
candidate behaves on InvokeModel (path A). Each feature result is recorded as
observed, with no fixed expectation; only the simple calls decide the model.

Hard limits: at most 12 model calls (retries disabled, so each request is one
call), max_tokens of at most 300 per call except the adaptive thinking call
(1,024), and no writes to the account. The planned upgrades (Claude Sonnet 5
and 5.5) are checked for availability only, never called.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any

import anthropic
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

REGION = "us-east-1"
TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"
# (inference profile ID, base model ID). The first candidate is preferred;
# the second is called only if the first one's simple call fails (ADR 0004).
PRIMARY = ("us.anthropic.claude-sonnet-4-6", "anthropic.claude-sonnet-4-6")
FALLBACK = ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "anthropic.claude-sonnet-4-5-20250929-v1:0")
UPGRADES = (
    ("us.anthropic.claude-sonnet-5", "anthropic.claude-sonnet-5"),
    ("us.anthropic.claude-sonnet-5-5", "anthropic.claude-sonnet-5-5"),
)
BEDROCK_ANTHROPIC_VERSION = "bedrock-2023-05-31"
MAX_MODEL_CALLS = 12
MAX_TOKENS = 300
MAX_THINKING_TOKENS = 1024

SIMPLE_PROMPT = "In one short sentence, what is Amazon Bedrock?"
CACHE_PROMPT = "Reply with the single word OK."
TOOL_PROMPT = "What is the weather in Lisbon right now? Use the get_weather tool."
WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Returns the current weather for a city.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["answer", "confidence"],
    "additionalProperties": False,
}


def cached_system_prompt() -> str:
    """About 1,300 tokens of deterministic text, above Sonnet 4.6's 1,024-token cache minimum."""
    lines = ["You are a careful assistant that answers questions about software delivery."]
    for number in range(1, 24):
        lines.append(
            f"Guideline {number}: when a question touches release step {number}, explain "
            "the reasoning in plain language, name the risk that the step reduces, say "
            "what would happen if the step were skipped, and keep the whole answer short "
            "enough to read in under a minute."
        )
    return "\n".join(lines)


CACHED_SYSTEM = [{"type": "text", "text": cached_system_prompt(), "cache_control": {"type": "ephemeral"}}]


class CallBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def spend(self, label: str) -> None:
        if self.used >= self.limit:
            raise RuntimeError(f"model call budget of {self.limit} exhausted before {label}")
        self.used += 1


class Report:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str) -> None:
        print(f"[{status}] {name}: {detail}")
        self.lines.append((status, name, detail))


class CallFailed(Exception):
    def __init__(self, status: int | None, text: str) -> None:
        super().__init__(text)
        self.status = status
        self.text = text


def client_error_text(error: ClientError) -> str:
    response = error.response
    return (
        f"{response['Error'].get('Code')} (HTTP {response['ResponseMetadata'].get('HTTPStatusCode')}): "
        f"{response['Error'].get('Message')} request_id={response['ResponseMetadata'].get('RequestId')}"
    )


def describe(payload: dict[str, Any], request_id: str) -> str:
    types = [block.get("type") for block in payload.get("content", [])]
    return f"stop_reason={payload.get('stop_reason')} blocks={types} request_id={request_id}"


def show_usage(label: str, payload: dict[str, Any]) -> None:
    print(f"     usage ({label}): {json.dumps(payload.get('usage', {}), sort_keys=True)}")


def first_text(payload: dict[str, Any]) -> str:
    return next((block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text"), "")


# --- 1 and 2. Availability and inference profiles (no model calls) ---------


def check_availability(report: Report, bedrock: Any) -> None:
    for _, model_id in (PRIMARY, FALLBACK, *UPGRADES):
        name = f"1. availability {model_id}"
        try:
            response = bedrock.get_foundation_model_availability(modelId=model_id)
        except ClientError as error:
            report.add("FAIL", name, client_error_text(error))
            continue
        fields = {key: value for key, value in response.items() if key != "ResponseMetadata"}
        report.add("INFO", name, json.dumps(fields, sort_keys=True, default=str))


def check_profiles(report: Report, bedrock: Any) -> None:
    for profile_id, _ in (PRIMARY, FALLBACK, *UPGRADES):
        name = f"2. inference profile {profile_id}"
        try:
            profile = bedrock.get_inference_profile(inferenceProfileIdentifier=profile_id)
        except ClientError as error:
            report.add("FAIL", name, client_error_text(error))
            continue
        model_arns = [model["modelArn"] for model in profile.get("models", [])]
        regions = sorted({arn.split(":")[3] for arn in model_arns})
        ok = profile.get("status") == "ACTIVE" and bool(model_arns)
        report.add("PASS" if ok else "FAIL", name, f"status={profile.get('status')} regions={regions}")
        for arn in model_arns:
            print(f"     destination model: {arn}")


# --- 3. Titan Text Embeddings V2 --------------------------------------------


def check_titan(report: Report, runtime: Any, budget: CallBudget) -> None:
    name = "3. Titan V2 InvokeModel"
    body = {
        "inputText": "Terraform remote state and keyless GitHub OIDC deploys",
        "dimensions": 1024,
        "normalize": True,
    }
    try:
        budget.spend(name)
        response = runtime.invoke_model(
            modelId=TITAN_MODEL_ID,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
    except ClientError as error:
        report.add("FAIL", name, client_error_text(error))
        return
    payload = json.loads(response["body"].read())
    vector = payload.get("embedding", [])
    norm = math.sqrt(sum(value * value for value in vector))
    ok = len(vector) == 1024 and abs(norm - 1.0) < 1e-3
    report.add(
        "PASS" if ok else "FAIL",
        name,
        f"dimensions={len(vector)} norm={norm:.4f} inputTextTokenCount={payload.get('inputTextTokenCount')} "
        f"request_id={response['ResponseMetadata']['RequestId']}",
    )


# --- 4 and 5. Claude on path A (InvokeModel with the us. profile) -----------


def invoke(runtime: Any, budget: CallBudget, profile_id: str, label: str, **fields: Any) -> tuple[dict[str, Any], str]:
    budget.spend(label)
    body = {"anthropic_version": BEDROCK_ANTHROPIC_VERSION, "max_tokens": MAX_TOKENS, **fields}
    try:
        response = runtime.invoke_model(
            modelId=profile_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
    except ClientError as error:
        raise CallFailed(error.response["ResponseMetadata"].get("HTTPStatusCode"), client_error_text(error)) from error
    return json.loads(response["body"].read()), response["ResponseMetadata"]["RequestId"]


def simple_call(report: Report, runtime: Any, budget: CallBudget, section: str, profile_id: str) -> bool:
    name = f"{section}a. {profile_id} simple call"
    try:
        payload, request_id = invoke(runtime, budget, profile_id, name, messages=[{"role": "user", "content": SIMPLE_PROMPT}])
    except CallFailed as error:
        report.add("FAIL", name, error.text)
        return False
    report.add("PASS", name, describe(payload, request_id))
    show_usage(name, payload)
    return True


def record_features(report: Report, runtime: Any, budget: CallBudget, section: str, profile_id: str) -> None:
    """Records each feature as ACCEPTED or REJECTED, with what was observed."""

    def attempt(key: str, title: str, observe: Any, **fields: Any) -> None:
        name = f"{section}{key}. {profile_id} {title}"
        try:
            payload, request_id = invoke(runtime, budget, profile_id, name, **fields)
        except CallFailed as error:
            report.add("REJECTED", name, error.text)
            return
        report.add("ACCEPTED", name, f"{observe(payload)}; {describe(payload, request_id)}")
        show_usage(name, payload)

    user = [{"role": "user", "content": SIMPLE_PROMPT}]
    tool_request = {"messages": [{"role": "user", "content": TOOL_PROMPT}], "tools": [WEATHER_TOOL]}

    def thinking_seen(payload: dict[str, Any]) -> str:
        return f"thinking blocks={sum(1 for b in payload.get('content', []) if b.get('type') == 'thinking')}"

    def json_seen(payload: dict[str, Any]) -> str:
        try:
            data = json.loads(first_text(payload))
        except ValueError:
            return "text is not JSON"
        return f"valid JSON with keys={sorted(data)}" if isinstance(data, dict) else "JSON is not an object"

    def tool_seen(payload: dict[str, Any]) -> str:
        return f"tool_use={'yes' if any(b.get('type') == 'tool_use' for b in payload.get('content', [])) else 'no'}"

    attempt(
        "b",
        "adaptive thinking, effort=low",
        thinking_seen,
        messages=user,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        max_tokens=MAX_THINKING_TOKENS,
    )
    attempt(
        "c",
        "structured output (output_config.format json_schema)",
        json_seen,
        messages=user,
        output_config={"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
    )
    attempt("d", "tool_choice auto", tool_seen, tool_choice={"type": "auto"}, **tool_request)
    attempt("e", "tool_choice forced", tool_seen, tool_choice={"type": "tool", "name": "get_weather"}, **tool_request)
    attempt("f", "temperature=0 without thinking", lambda payload: f"text={first_text(payload)[:60]!r}", messages=user, temperature=0)

    name = f"{section}g. {profile_id} prompt cache (two calls)"
    usages = []
    try:
        for number in (1, 2):
            payload, request_id = invoke(
                runtime,
                budget,
                profile_id,
                f"{name} #{number}",
                system=CACHED_SYSTEM,
                messages=[{"role": "user", "content": CACHE_PROMPT}],
            )
            usages.append(payload.get("usage", {}))
            print(f"     cache call {number}: {describe(payload, request_id)}")
            show_usage(f"{name} #{number}", payload)
    except CallFailed as error:
        report.add("REJECTED", name, error.text)
        return
    created = usages[0].get("cache_creation_input_tokens") or 0
    read = usages[1].get("cache_read_input_tokens") or 0
    report.add("ACCEPTED", name, f"first call created={created}, second call read={read}")


def mark_not_tested(report: Report, section: str, profile_id: str) -> None:
    for key, title in (
        ("b", "adaptive thinking, effort=low"),
        ("c", "structured output (output_config.format json_schema)"),
        ("d", "tool_choice auto"),
        ("e", "tool_choice forced"),
        ("f", "temperature=0 without thinking"),
        ("g", "prompt cache (two calls)"),
    ):
        report.add("NOT TESTED", f"{section}{key}. {profile_id} {title}", "the simple call failed, so this feature was not called")


# --- Summary ------------------------------------------------------------------


def print_summary(report: Report, budget: CallBudget, chosen: str | None) -> None:
    print()
    print("=" * 30 + " SUMMARY (paste this) " + "=" * 30)
    print(
        f"python={sys.version.split()[0]} anthropic={anthropic.__version__} boto3={boto3.__version__} "
        f"region={REGION} model_calls={budget.used}/{budget.limit} "
        f"max_tokens={MAX_TOKENS} (thinking call {MAX_THINKING_TOKENS})"
    )
    for status, name, detail in report.lines:
        print(f"[{status}] {name}: {detail}")
    if chosen:
        print(f"Model rule: the simple call passed on {chosen} -> choose {chosen} on path A (InvokeModel)")
    else:
        print("Model rule: STOP - neither Claude Sonnet 4.6 nor Claude Sonnet 4.5 accepted the simple call")
    print("=" * 82)


def main() -> None:
    report = Report()
    budget = CallBudget(MAX_MODEL_CALLS)
    bedrock = boto3.client("bedrock", region_name=REGION)
    runtime = boto3.client(
        "bedrock-runtime",
        region_name=REGION,
        config=Config(retries={"max_attempts": 1, "mode": "standard"}, read_timeout=120),
    )
    chosen: list[str] = []

    def run_candidates() -> None:
        primary_profile, fallback_profile = PRIMARY[0], FALLBACK[0]
        if simple_call(report, runtime, budget, "4", primary_profile):
            chosen.append(primary_profile)
            record_features(report, runtime, budget, "4", primary_profile)
            report.add("NOT TESTED", f"5a. {fallback_profile} simple call", "called only when the primary model fails")
            return
        mark_not_tested(report, "4", primary_profile)
        if simple_call(report, runtime, budget, "5", fallback_profile):
            chosen.append(fallback_profile)

    sections = (
        lambda: check_availability(report, bedrock),
        lambda: check_profiles(report, bedrock),
        lambda: check_titan(report, runtime, budget),
        run_candidates,
    )
    for section in sections:
        try:
            section()
        except Exception as error:  # keep going so the summary always prints
            report.add("FAIL", "unexpected error", f"{type(error).__name__}: {error}")
    print_summary(report, budget, chosen[0] if chosen else None)


if __name__ == "__main__":
    main()
