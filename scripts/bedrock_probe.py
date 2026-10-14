"""Read-only Bedrock probe for fit-check, stage 2.

Run in AWS CloudShell through scripts/bedrock_probe.sh, with the console
session's credentials. It reports the account's availability for the Claude
generation model and its planned upgrade, the inference profiles, Titan Text
Embeddings V2, and the generation model on both invocation paths. It prints
PASS, FAIL, NOT TESTED or INFO for each check and ends with a summary to paste
back.

Hard limits: at most 12 model calls (retries disabled, so each request is one
call), max_tokens of at most 300 per call, and no writes to the account. The
upgrade model is checked for availability only, never called.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from typing import Any, Callable

import anthropic
import boto3
from anthropic import AnthropicBedrockMantle
from botocore.config import Config
from botocore.exceptions import ClientError

REGION = "us-east-1"
TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"
# Generation model under test (ADR 0004), and the upgrade the account is not
# entitled to yet.
SONNET_PROFILE_ID = "us.anthropic.claude-sonnet-5"
SONNET_MODEL_ID = "anthropic.claude-sonnet-5"
UPGRADE_PROFILE_ID = "us.anthropic.claude-sonnet-5-5"
UPGRADE_MODEL_ID = "anthropic.claude-sonnet-5-5"
BEDROCK_ANTHROPIC_VERSION = "bedrock-2023-05-31"
MAX_MODEL_CALLS = 12
MAX_TOKENS = 300

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
LOW_EFFORT = {"effort": "low"}


def long_system_prompt() -> str:
    """About 2,000 deterministic words: above every model's cache minimum (1,024 tokens on Sonnet 5)."""
    lines = ["You are a careful assistant that answers questions about software delivery."]
    for number in range(1, 41):
        lines.append(
            f"Guideline {number}: when a question touches release step {number}, explain "
            "the reasoning in plain language, name the risk that the step reduces, say "
            "what would happen if the step were skipped, and keep the whole answer short "
            "enough to read in under a minute."
        )
    return "\n".join(lines)


CACHED_SYSTEM = [{"type": "text", "text": long_system_prompt(), "cache_control": {"type": "ephemeral"}}]


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

    def status_of(self, name_prefix: str) -> str | None:
        for status, name, _ in self.lines:
            if name.startswith(name_prefix):
                return status
        return None


class CallFailed(Exception):
    def __init__(self, status: int | None, text: str) -> None:
        super().__init__(text)
        self.status = status
        self.text = text


@dataclass
class Reply:
    stop_reason: str | None
    block_types: list[str]
    usage: dict[str, Any]
    ids: str

    def describe(self) -> str:
        return f"stop_reason={self.stop_reason} blocks={self.block_types} {self.ids}"


Caller = Callable[..., Reply]


def client_error_text(error: ClientError) -> str:
    response = error.response
    return (
        f"{response['Error'].get('Code')} (HTTP {response['ResponseMetadata'].get('HTTPStatusCode')}): "
        f"{response['Error'].get('Message')} request_id={response['ResponseMetadata'].get('RequestId')}"
    )


def show_usage(label: str, usage: dict[str, Any]) -> None:
    print(f"     usage ({label}): {json.dumps(usage, sort_keys=True)}")


# --- 1. Availability and inference profiles (no model calls) ----------------


def check_availability(report: Report, bedrock: Any) -> None:
    for model_id in (UPGRADE_MODEL_ID, SONNET_MODEL_ID):
        name = f"1. availability {model_id}"
        try:
            response = bedrock.get_foundation_model_availability(modelId=model_id)
        except ClientError as error:
            report.add("FAIL", name, client_error_text(error))
            continue
        fields = {key: value for key, value in response.items() if key != "ResponseMetadata"}
        report.add("INFO", name, json.dumps(fields, sort_keys=True, default=str))


def check_profiles(report: Report, bedrock: Any) -> None:
    for profile_id in (SONNET_PROFILE_ID, UPGRADE_PROFILE_ID):
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


# --- 4 and 5. The generation model on each invocation path ------------------


def caller_a(runtime: Any, budget: CallBudget) -> Caller:
    """Path A: InvokeModel on bedrock-runtime with the us. inference profile."""

    def call(label: str, **fields: Any) -> Reply:
        budget.spend(label)
        body = {"anthropic_version": BEDROCK_ANTHROPIC_VERSION, "max_tokens": MAX_TOKENS, **fields}
        try:
            response = runtime.invoke_model(
                modelId=SONNET_PROFILE_ID,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
        except ClientError as error:
            raise CallFailed(error.response["ResponseMetadata"].get("HTTPStatusCode"), client_error_text(error)) from error
        payload = json.loads(response["body"].read())
        return Reply(
            payload.get("stop_reason"),
            [block.get("type") for block in payload.get("content", [])],
            payload.get("usage", {}),
            f"request_id={response['ResponseMetadata']['RequestId']}",
        )

    return call


def caller_b(budget: CallBudget) -> Caller:
    """Path B: the bedrock-mantle Messages endpoint with AnthropicBedrockMantle."""
    client = AnthropicBedrockMantle(aws_region=REGION, max_retries=0)

    def call(label: str, **fields: Any) -> Reply:
        budget.spend(label)
        try:
            raw = client.messages.with_raw_response.create(model=SONNET_MODEL_ID, max_tokens=MAX_TOKENS, **fields)
        except anthropic.APIStatusError as error:
            headers = error.response.headers
            raise CallFailed(
                error.status_code,
                f"HTTP {error.status_code}: {error.message} request-id={headers.get('request-id')} "
                f"x-amzn-requestid={headers.get('x-amzn-requestid')}",
            ) from error
        except anthropic.APIError as error:
            raise CallFailed(None, f"{type(error).__name__}: {error}") from error
        message = raw.parse()
        return Reply(
            message.stop_reason,
            [block.type for block in message.content],
            message.usage.to_dict(),
            f"request-id={raw.headers.get('request-id')} x-amzn-requestid={raw.headers.get('x-amzn-requestid')}",
        )

    return call


def check_path(report: Report, section: int, path: str, call: Caller, include_forced: bool) -> bool:
    """Runs the path's checks; returns True when effort, auto tool use and the cache all pass."""
    names = {
        "simple": f"{section}a. path {path} simple call",
        "effort": f"{section}b. path {path} effort=low",
        "tool": f"{section}c. path {path} tool_choice auto",
        "forced": f"{section}d. path {path} tool_choice forced (result recorded, no expectation)",
        "cache": f"{section}e. path {path} prompt cache",
    }
    if not include_forced:
        del names["forced"]
    user = [{"role": "user", "content": SIMPLE_PROMPT}]

    try:
        reply = call(names["simple"], messages=user)
        report.add("PASS", names["simple"], reply.describe())
        show_usage(names["simple"], reply.usage)
    except CallFailed as error:
        report.add("FAIL", names["simple"], error.text)
        for key, name in names.items():
            if key != "simple":
                report.add("NOT TESTED", name, "the basic call failed, so this feature was not called")
        return False

    effort: dict[str, Any] = {}
    try:
        reply = call(names["effort"], messages=user, output_config=LOW_EFFORT)
        report.add("PASS", names["effort"], reply.describe())
        show_usage(names["effort"], reply.usage)
        effort = {"output_config": LOW_EFFORT}
    except CallFailed as error:
        report.add("FAIL", names["effort"], error.text)

    # Low effort keeps adaptive thinking inside max_tokens; it is sent only when
    # the path accepted it, so each check fails for its own reason.
    tool_request = {"messages": [{"role": "user", "content": TOOL_PROMPT}], "tools": [WEATHER_TOOL]}
    tool_ok = False
    try:
        reply = call(names["tool"], tool_choice={"type": "auto"}, **tool_request, **effort)
        tool_ok = "tool_use" in reply.block_types
        report.add("PASS" if tool_ok else "FAIL", names["tool"], reply.describe())
        show_usage(names["tool"], reply.usage)
    except CallFailed as error:
        report.add("FAIL", names["tool"], error.text)

    if include_forced:
        try:
            reply = call(names["forced"], tool_choice={"type": "tool", "name": "get_weather"}, **tool_request)
            report.add("INFO", names["forced"], "accepted; " + reply.describe())
            show_usage(names["forced"], reply.usage)
        except CallFailed as error:
            report.add("INFO", names["forced"], "rejected; " + error.text)

    cache_ok = False
    try:
        usages = []
        for attempt in (1, 2):
            reply = call(
                f"{names['cache']} #{attempt}",
                system=CACHED_SYSTEM,
                messages=[{"role": "user", "content": CACHE_PROMPT}],
                **effort,
            )
            usages.append(reply.usage)
            print(f"     cache call {attempt}: {reply.describe()}")
            show_usage(f"{names['cache']} #{attempt}", reply.usage)
        created = usages[0].get("cache_creation_input_tokens") or 0
        read = usages[1].get("cache_read_input_tokens") or 0
        cache_ok = created > 0 and read > 0
        report.add("PASS" if cache_ok else "FAIL", names["cache"], f"first call created={created}, second call read={read}")
    except CallFailed as error:
        report.add("FAIL", names["cache"], error.text)

    return bool(effort) and tool_ok and cache_ok


# --- Summary ------------------------------------------------------------------


def print_summary(report: Report, budget: CallBudget, path_ok: dict[str, bool]) -> None:
    print()
    print("=" * 30 + " SUMMARY (paste this) " + "=" * 30)
    print(
        f"python={sys.version.split()[0]} anthropic={anthropic.__version__} boto3={boto3.__version__} "
        f"region={REGION} model={SONNET_PROFILE_ID} / {SONNET_MODEL_ID} "
        f"model_calls={budget.used}/{budget.limit} max_tokens={MAX_TOKENS}"
    )
    for status, name, detail in report.lines:
        print(f"[{status}] {name}: {detail}")
    if path_ok.get("A"):
        verdict = "choose A (InvokeModel, us. profile)"
    elif path_ok.get("B"):
        verdict = "choose B (bedrock-mantle)"
    else:
        verdict = "no path accepted effort, auto tool use and the prompt cache: no path chosen"
    print(f"Path rule: A={path_ok.get('A', False)} B={path_ok.get('B', False)} -> {verdict}")
    print("=" * 82)


def main() -> None:
    report = Report()
    budget = CallBudget(MAX_MODEL_CALLS)
    path_ok: dict[str, bool] = {}
    bedrock = boto3.client("bedrock", region_name=REGION)
    runtime = boto3.client(
        "bedrock-runtime",
        region_name=REGION,
        config=Config(retries={"max_attempts": 1, "mode": "standard"}, read_timeout=120),
    )

    def run_path_a() -> None:
        path_ok["A"] = check_path(report, 4, "A", caller_a(runtime, budget), include_forced=True)

    def run_path_b() -> None:
        path_ok["B"] = check_path(report, 5, "B", caller_b(budget), include_forced=False)

    sections = (
        lambda: check_availability(report, bedrock),
        lambda: check_profiles(report, bedrock),
        lambda: check_titan(report, runtime, budget),
        run_path_a,
        run_path_b,
    )
    for section in sections:
        try:
            section()
        except Exception as error:  # keep going so the summary always prints
            report.add("FAIL", "unexpected error", f"{type(error).__name__}: {error}")
    print_summary(report, budget, path_ok)


if __name__ == "__main__":
    main()
