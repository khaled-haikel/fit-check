"""Extraction: the requirements of a job description, each traced to a verbatim source."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from .assets import Assets
from .costs import CallGuard, CallRecord
from .errors import INVALID_MODEL_OUTPUT, AnalysisError
from .jsonout import parse_json_object
from .llm import CallSettings, ClaudeReply, call_claude
from .normalize import normalize_for_match

PHASE = "extract"
MAX_ERRORS_SHOWN = 20
INSTRUCTION = "Extract the requirements of the job description above, following your instructions. Reply with the JSON object only."

Category = Literal["skill", "experience", "education", "language", "location", "domain", "soft"]


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    text_en: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    type: Literal["must", "nice"]
    category: Category


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requirements: list[ExtractedItem]


@dataclass(frozen=True)
class Requirement:
    id: str
    source: str
    text_en: str
    type: str
    category: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class ExtractionResult:
    requirements: list[Requirement]
    # Requirements whose source is not in the job description.
    dropped_unsourced: int = 0
    dropped_duplicates: int = 0
    dropped_over_cap: int = 0
    # Problems of the first reply; non-empty means it was retried.
    first_attempt_errors: list[str] = field(default_factory=list)
    records: list[CallRecord] = field(default_factory=list)

    @property
    def retried(self) -> bool:
        return bool(self.first_attempt_errors)


def job_description_block(job_description: str) -> str:
    return f"<job_description>\n{job_description}\n</job_description>\n\n{INSTRUCTION}"


def correction_block(errors: Sequence[str], previous: str | None) -> str:
    """Appended to the same single-turn request for the one retry; never an assistant turn."""
    listed = "\n".join(f"- {error}" for error in errors[:MAX_ERRORS_SHOWN])
    return (
        "Your previous reply could not be used. Its problems:\n"
        f"{listed}\n\n"
        f"<previous_response>\n{previous or ''}\n</previous_response>\n\n"
        "Reply again with the complete, corrected JSON object only."
    )


def _validation_messages(error: ValidationError) -> list[str]:
    return [f"{'.'.join(str(part) for part in item['loc']) or '(root)'}: {item['msg']}" for item in error.errors()]


def read_reply(reply: ClaudeReply) -> tuple[ExtractionOutput | None, list[str]]:
    """The validated output, or the problems that make the reply unusable."""
    if reply.stop_reason == "max_tokens":
        return None, ["the reply stopped at max_tokens before the JSON object was complete"]
    if reply.text is None:
        return None, ["the reply has no text"]
    try:
        data = parse_json_object(reply.text)
    except ValueError as error:
        return None, [str(error)]
    try:
        return ExtractionOutput.model_validate(data), []
    except ValidationError as error:
        return None, _validation_messages(error)


def select_requirements(output: ExtractionOutput, job_description: str, max_requirements: int) -> ExtractionResult:
    """Keeps sourced, distinct requirements in the order of the job description, numbered r01, r02, ..."""
    haystack = normalize_for_match(job_description)
    kept: list[tuple[int, int, ExtractedItem]] = []
    seen: set[str] = set()
    unsourced = duplicates = 0
    for order, item in enumerate(output.requirements):
        position = haystack.find(normalize_for_match(item.source))
        if not normalize_for_match(item.source) or position < 0:
            unsourced += 1
            continue
        key = normalize_for_match(item.text_en)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        kept.append((position, order, item))
    kept.sort(key=lambda entry: (entry[0], entry[1]))
    over_cap = max(0, len(kept) - max_requirements)
    requirements = [
        Requirement(id=f"r{number:02d}", source=item.source, text_en=item.text_en, type=item.type, category=item.category)
        for number, (_, _, item) in enumerate(kept[:max_requirements], start=1)
    ]
    return ExtractionResult(requirements, dropped_unsourced=unsourced, dropped_duplicates=duplicates, dropped_over_cap=over_cap)


def extract_requirements(job_description: str, assets: Assets, client: Any, guards: Sequence[CallGuard]) -> ExtractionResult:
    """job_description must already be normalized (normalize_job_description)."""
    config: dict[str, Any] = assets.analysis["extract"]
    settings = CallSettings.from_config(config)
    price = assets.price(assets.generation_model)
    user = [job_description_block(job_description)]

    reply = call_claude(client, assets.generation_model, price, assets.extract_system, user, settings, PHASE, 1, guards)
    records = [reply.record]
    output, errors = read_reply(reply)
    first_errors = list(errors)
    if output is None:
        retry = call_claude(
            client,
            assets.generation_model,
            price,
            assets.extract_system,
            [*user, correction_block(errors, reply.text)],
            settings,
            PHASE,
            2,
            guards,
        )
        records.append(retry.record)
        output, errors = read_reply(retry)
        if output is None:
            raise AnalysisError(INVALID_MODEL_OUTPUT, f"{PHASE}: {'; '.join(errors[:3])}")

    result = select_requirements(output, job_description, int(config["max_requirements"]))
    result.first_attempt_errors = first_errors
    result.records = records
    return result
