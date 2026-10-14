"""Read-only Bedrock probe for fit-check, stage 2.

Run in AWS CloudShell through scripts/bedrock_probe.sh, with the console
session's credentials. Checks the facts the stage 2 plan relies on, prints
PASS or FAIL with details for each check and MATCH or MISMATCH for each
documented fact, and ends with a summary to paste back.

Hard limits: at most 12 model calls (retries disabled, so each request is
one call), max_tokens of at most 300 per call, and no writes to the account.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any

import anthropic
import boto3
from anthropic import AnthropicBedrockMantle
from botocore.config import Config
from botocore.exceptions import ClientError

REGION = "us-east-1"
SONNET_PROFILE_ID = "us.anthropic.claude-sonnet-5-5"
SONNET_MANTLE_MODEL = "anthropic.claude-sonnet-5-5"
TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"
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
    """About 1,300 deterministic words: far above the 512-token cache minimum."""
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
        self.checks: list[tuple[str, str, str]] = []
        self.facts: list[tuple[str, str, str]] = []

    def check(self, name: str, ok: bool, detail: str) -> None:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}: {detail}")
        self.checks.append((status, name, detail))

    def fact(self, statement: str, matches: bool, evidence: str) -> None:
        tag = "MATCH" if matches else "MISMATCH"
        print(f"    {tag}: {statement} ({evidence})")
        self.facts.append((tag, statement, evidence))

    def passed(self, name_prefix: str) -> bool:
        return any(status == "PASS" and name.startswith(name_prefix) for status, name, _ in self.checks)


def client_error_text(error: ClientError) -> str:
    response = error.response
    return (
        f"{response['Error'].get('Code')} (HTTP {response['ResponseMetadata'].get('HTTPStatusCode')}): "
        f"{response['Error'].get('Message')} request_id={response['ResponseMetadata'].get('RequestId')}"
    )


def api_error_text(error: anthropic.APIStatusError) -> str:
    headers = error.response.headers
    return (
        f"HTTP {error.status_code}: {error.message} "
        f"request-id={headers.get('request-id')} x-amzn-requestid={headers.get('x-amzn-requestid')}"
    )


def show_usage(label: str, usage: dict[str, Any]) -> None:
    print(f"     usage ({label}): {json.dumps(usage, sort_keys=True)}")


# --- 1. Inference profile ---------------------------------------------------


def check_inference_profile(report: Report) -> None:
    bedrock = boto3.client("bedrock", region_name=REGION)
    name = "1. get-inference-profile"
    try:
        profile = bedrock.get_inference_profile(inferenceProfileIdentifier=SONNET_PROFILE_ID)
    except ClientError as error:
        report.check(name, False, client_error_text(error))
        return
    model_arns = [model["modelArn"] for model in profile.get("models", [])]
    regions = sorted({arn.split(":")[3] for arn in model_arns})
    report.check(
        name,
        profile.get("status") == "ACTIVE" and bool(model_arns),
        f"id={SONNET_PROFILE_ID} status={profile.get('status')} type={profile.get('type')} regions={regions}",
    )
    for arn in model_arns:
        print(f"     destination model: {arn}")


# --- 2. Titan Text Embeddings V2 --------------------------------------------


def check_titan(report: Report, runtime: Any, budget: CallBudget) -> None:
    name = "2. Titan V2 InvokeModel"
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
        report.check(name, False, client_error_text(error))
        return
    payload = json.loads(response["body"].read())
    vector = payload.get("embedding", [])
    norm = math.sqrt(sum(value * value for value in vector))
    report.check(
        name,
        len(vector) == 1024,
        f"dimensions={len(vector)} norm={norm:.4f} inputTextTokenCount={payload.get('inputTextTokenCount')} "
        f"request_id={response['ResponseMetadata']['RequestId']}",
    )
    report.fact(
        "Titan V2 returns 1,024 normalized dimensions",
        len(vector) == 1024 and abs(norm - 1.0) < 1e-3,
        f"len={len(vector)}, norm={norm:.4f}",
    )


# --- 3. Sonnet 5.5, path A: InvokeModel with the us. inference profile ------


def invoke_a(runtime: Any, budget: CallBudget, label: str, **fields: Any) -> tuple[dict[str, Any], str]:
    budget.spend(label)
    body = {"anthropic_version": BEDROCK_ANTHROPIC_VERSION, "max_tokens": MAX_TOKENS, **fields}
    response = runtime.invoke_model(
        modelId=SONNET_PROFILE_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read()), response["ResponseMetadata"]["RequestId"]


def describe_a(payload: dict[str, Any], request_id: str) -> str:
    types = [block.get("type") for block in payload.get("content", [])]
    return f"stop_reason={payload.get('stop_reason')} blocks={types} request_id={request_id}"


def check_path_a(report: Report, runtime: Any, budget: CallBudget) -> None:
    user = [{"role": "user", "content": SIMPLE_PROMPT}]

    name = "3a. path A simple call"
    try:
        payload, request_id = invoke_a(runtime, budget, name, messages=user)
        report.check(name, True, describe_a(payload, request_id))
        show_usage(name, payload.get("usage", {}))
    except ClientError as error:
        report.check(name, False, client_error_text(error))

    name = "3b. path A effort=low"
    effort = {}
    try:
        payload, request_id = invoke_a(runtime, budget, name, messages=user, output_config=LOW_EFFORT)
        report.check(name, True, describe_a(payload, request_id))
        show_usage(name, payload.get("usage", {}))
        report.fact("output_config.effort is accepted on path A", True, f"request_id={request_id}")
        effort = {"output_config": LOW_EFFORT}
    except ClientError as error:
        report.check(name, False, client_error_text(error))
        report.fact("output_config.effort is accepted on path A", False, client_error_text(error))

    # Low effort keeps adaptive thinking inside max_tokens. It is only sent
    # when 3b showed the path accepts it, so each check fails for its own reason.
    tool_request = {"messages": [{"role": "user", "content": TOOL_PROMPT}], "tools": [WEATHER_TOOL]}

    name = "3c. path A tool_choice auto"
    try:
        payload, request_id = invoke_a(runtime, budget, name, tool_choice={"type": "auto"}, **tool_request, **effort)
        called = any(block.get("type") == "tool_use" for block in payload.get("content", []))
        report.check(name, called, describe_a(payload, request_id))
        show_usage(name, payload.get("usage", {}))
        report.fact("with tool_choice auto, the model calls the tool", called, f"request_id={request_id}")
    except ClientError as error:
        report.check(name, False, client_error_text(error))

    name = "3d. path A forced tool_choice is rejected"
    try:
        payload, request_id = invoke_a(
            runtime, budget, name, tool_choice={"type": "tool", "name": "get_weather"}, **tool_request
        )
        report.check(name, False, "the request succeeded; " + describe_a(payload, request_id))
        report.fact("forced tool_choice returns an error", False, f"accepted, request_id={request_id}")
    except ClientError as error:
        status = error.response["ResponseMetadata"].get("HTTPStatusCode")
        report.check(name, status == 400, client_error_text(error))
        report.fact("forced tool_choice returns an error", status == 400, f"HTTP {status}")

    name = "3e. path A prompt cache"
    try:
        usages = []
        for attempt in (1, 2):
            payload, request_id = invoke_a(
                runtime,
                budget,
                f"{name} #{attempt}",
                system=CACHED_SYSTEM,
                messages=[{"role": "user", "content": CACHE_PROMPT}],
                **effort,
            )
            usage = payload.get("usage", {})
            usages.append(usage)
            print(f"     call {attempt}: {describe_a(payload, request_id)}")
            show_usage(f"{name} #{attempt}", usage)
        created = usages[0].get("cache_creation_input_tokens") or 0
        read = usages[1].get("cache_read_input_tokens") or 0
        report.check(name, created > 0 and read > 0, f"first call created={created}, second call read={read}")
        report.fact("explicit cache_control creates then reads the cache", created > 0 and read > 0, f"{created}/{read}")
    except ClientError as error:
        report.check(name, False, client_error_text(error))


# --- 4. Sonnet 5.5, path B: bedrock-mantle with AnthropicBedrockMantle ------


def create_b(client: AnthropicBedrockMantle, budget: CallBudget, label: str, **kwargs: Any) -> tuple[Any, str]:
    budget.spend(label)
    raw = client.messages.with_raw_response.create(model=SONNET_MANTLE_MODEL, max_tokens=MAX_TOKENS, **kwargs)
    ids = f"request-id={raw.headers.get('request-id')} x-amzn-requestid={raw.headers.get('x-amzn-requestid')}"
    return raw.parse(), ids


def describe_b(message: Any, ids: str) -> str:
    return f"stop_reason={message.stop_reason} blocks={[block.type for block in message.content]} {ids}"


def check_path_b(report: Report, budget: CallBudget) -> None:
    client = AnthropicBedrockMantle(aws_region=REGION, max_retries=0)
    user = [{"role": "user", "content": SIMPLE_PROMPT}]

    name = "4a. path B simple call"
    try:
        message, ids = create_b(client, budget, name, messages=user)
        report.check(name, True, describe_b(message, ids))
        show_usage(name, message.usage.to_dict())
    except anthropic.APIStatusError as error:
        report.check(name, False, api_error_text(error))
    except anthropic.APIError as error:
        report.check(name, False, f"{type(error).__name__}: {error}")

    name = "4b. path B effort=low"
    effort = {}
    try:
        message, ids = create_b(client, budget, name, messages=user, output_config=LOW_EFFORT)
        report.check(name, True, describe_b(message, ids))
        show_usage(name, message.usage.to_dict())
        report.fact("output_config.effort is accepted on path B", True, ids)
        effort = {"output_config": LOW_EFFORT}
    except anthropic.APIStatusError as error:
        report.check(name, False, api_error_text(error))
        report.fact("output_config.effort is accepted on path B", False, api_error_text(error))
    except anthropic.APIError as error:
        report.check(name, False, f"{type(error).__name__}: {error}")

    name = "4c. path B prompt cache"
    try:
        usages = []
        for attempt in (1, 2):
            message, ids = create_b(
                client,
                budget,
                f"{name} #{attempt}",
                system=CACHED_SYSTEM,
                messages=[{"role": "user", "content": CACHE_PROMPT}],
                **effort,
            )
            usage = message.usage.to_dict()
            usages.append(usage)
            print(f"     call {attempt}: {describe_b(message, ids)}")
            show_usage(f"{name} #{attempt}", usage)
        created = usages[0].get("cache_creation_input_tokens") or 0
        read = usages[1].get("cache_read_input_tokens") or 0
        report.check(name, created > 0 and read > 0, f"first call created={created}, second call read={read}")
    except anthropic.APIStatusError as error:
        report.check(name, False, api_error_text(error))
    except anthropic.APIError as error:
        report.check(name, False, f"{type(error).__name__}: {error}")


# --- Summary ------------------------------------------------------------------


def print_summary(report: Report, budget: CallBudget) -> None:
    path_a_ok = all(report.passed(prefix) for prefix in ("3b.", "3c.", "3e."))
    print()
    print("=" * 30 + " SUMMARY (paste this) " + "=" * 30)
    print(
        f"python={sys.version.split()[0]} anthropic={anthropic.__version__} boto3={boto3.__version__} "
        f"region={REGION} model_calls={budget.used}/{budget.limit} max_tokens={MAX_TOKENS}"
    )
    for status, name, detail in report.checks:
        print(f"[{status}] {name}: {detail}")
    print("Documented facts:")
    for tag, statement, evidence in report.facts:
        print(f"  {tag}: {statement} ({evidence})")
    choice = "A (InvokeModel, us. profile)" if path_a_ok else "B (bedrock-mantle)"
    print(f"Path rule: path A accepted effort, prompt cache and auto tool use = {path_a_ok} -> choose {choice}")
    print("=" * 82)


def main() -> None:
    report = Report()
    budget = CallBudget(MAX_MODEL_CALLS)
    runtime = boto3.client(
        "bedrock-runtime",
        region_name=REGION,
        config=Config(retries={"max_attempts": 1, "mode": "standard"}, read_timeout=120),
    )
    sections = (
        lambda: check_inference_profile(report),
        lambda: check_titan(report, runtime, budget),
        lambda: check_path_a(report, runtime, budget),
        lambda: check_path_b(report, budget),
    )
    for section in sections:
        try:
            section()
        except Exception as error:  # keep going so the summary always prints
            report.check("unexpected error", False, f"{type(error).__name__}: {error}")
    print_summary(report, budget)


if __name__ == "__main__":
    main()
