"""Judgment: a verdict per requirement, grounded in quotes the code checks (fail closed).

The judge never sees the job description, only the requirements and the
cards, so an instruction injected into the posting can reach the extraction
at most.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .assets import Assets
from .costs import CallGuard, CallRecord
from .errors import INVALID_MODEL_OUTPUT, AnalysisError
from .extract import correction_block
from .jsonout import parse_json_object
from .llm import CallSettings, ClaudeReply, call_claude
from .normalize import appears_in
from .retrieve import Candidates

PHASE = "judge"
INSTRUCTION = "Judge every requirement above against the cards, following your instructions. Reply with the JSON object only."
POSITIVE = ("meets", "partial")
QUOTE_MIN_CHARS = 12
QUOTE_MAX_CHARS = 300
REASON_MAX_CHARS = 200
DOWNGRADED_REASON = "No valid citation supported a higher verdict."
MISSING_REASON = "No valid judgment was returned for this requirement."

Verdict = Literal["meets", "partial", "does_not_meet"]


class CitationOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    card_id: str
    quote: str


class JudgmentOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requirement_id: str
    verdict: Verdict
    citations: list[CitationOut] = []
    reason: str = ""


class JudgeOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    judgments: list[JudgmentOut]


@dataclass(frozen=True)
class Citation:
    card_id: str
    quote: str


@dataclass(frozen=True)
class Judgment:
    requirement_id: str
    verdict: str
    citations: tuple[Citation, ...]
    reason: str
    downgraded: bool = False


@dataclass
class JudgeResult:
    judgments: list[Judgment]
    # Problems of the first reply; non-empty means it was retried.
    first_attempt_errors: list[str] = field(default_factory=list)
    dropped_citations: int = 0
    records: list[CallRecord] = field(default_factory=list)

    @property
    def retried(self) -> bool:
        return bool(self.first_attempt_errors)

    @property
    def downgraded(self) -> int:
        return sum(1 for judgment in self.judgments if judgment.downgraded)


def card_block(card: Mapping[str, Any]) -> str:
    lines = [f'<card id="{card["id"]}" kind="{card["kind"]}">', f"title: {card['title']}"]
    if card.get("period"):
        lines.append(f"period: {card['period']}")
    lines.append(f"summary: {card['summary']}")
    lines.append("claims:")
    lines += [f"- {claim}" for claim in card["claims"]]
    lines.append("</card>")
    return "\n".join(lines)


def requirements_block(requirements: Sequence, candidates: Mapping[str, Candidates]) -> str:
    blocks = []
    for requirement in requirements:
        allowed = candidates[requirement.id]
        blocks.append(
            f'<requirement id="{requirement.id}" type="{requirement.type}" category="{requirement.category}">\n'
            f"text: {requirement.text_en}\n"
            f"evidence_cards: {', '.join(allowed.evidence) or 'none'}\n"
            f"gap_card: {allowed.gap or 'none'}\n"
            "</requirement>"
        )
    return "\n".join(blocks)


def user_content(requirements: Sequence, candidates: Mapping[str, Candidates], cards: Mapping[str, Mapping[str, Any]]) -> str:
    """Every candidate card once, then the requirements with the cards each may cite, then the instruction."""
    ordered: list[str] = []
    for requirement in requirements:
        for card_id in candidates[requirement.id].allowed:
            if card_id not in ordered:
                ordered.append(card_id)
    cards_text = "\n".join(card_block(cards[card_id]) for card_id in ordered)
    return (
        f"<cards>\n{cards_text}\n</cards>\n\n"
        f"<requirements>\n{requirements_block(requirements, candidates)}\n</requirements>\n\n"
        f"{INSTRUCTION}"
    )


def citation_problem(
    citation: CitationOut, verdict: str, allowed: Candidates, cards: Mapping[str, Mapping[str, Any]]
) -> str | None:
    """Why a citation is invalid, or None. Evidence cards back meets and partial; the gap card only does_not_meet."""
    if verdict in POSITIVE and citation.card_id not in allowed.evidence:
        return f"cites {citation.card_id}, which is not one of its evidence cards"
    if verdict == "does_not_meet" and citation.card_id != allowed.gap:
        return f"cites {citation.card_id}; does_not_meet may cite only its gap card"
    if not QUOTE_MIN_CHARS <= len(citation.quote) <= QUOTE_MAX_CHARS:
        return f"quote from {citation.card_id} has {len(citation.quote)} characters, outside {QUOTE_MIN_CHARS} to {QUOTE_MAX_CHARS}"
    card = cards[citation.card_id]
    if not appears_in(citation.quote, card["title"], card["summary"], *card["claims"]):
        return f"quote from {citation.card_id} does not appear in that card"
    return None


def review(output: JudgeOutput, requirements: Sequence, candidates: Mapping[str, Candidates], cards: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Every problem in a parsed reply: coverage, citations and reason length."""
    problems = []
    expected = [requirement.id for requirement in requirements]
    counts: dict[str, int] = {}
    for judgment in output.judgments:
        counts[judgment.requirement_id] = counts.get(judgment.requirement_id, 0) + 1
        if judgment.requirement_id not in candidates:
            problems.append(f"{judgment.requirement_id}: unknown requirement id")
            continue
        if counts[judgment.requirement_id] > 1:
            continue
        allowed = candidates[judgment.requirement_id]
        for citation in judgment.citations:
            problem = citation_problem(citation, judgment.verdict, allowed, cards)
            if problem:
                problems.append(f"{judgment.requirement_id}: {problem}")
        if judgment.verdict in POSITIVE and not any(
            citation_problem(citation, judgment.verdict, allowed, cards) is None for citation in judgment.citations
        ):
            problems.append(f"{judgment.requirement_id}: {judgment.verdict} needs at least one valid citation of its evidence cards")
        if len(judgment.reason) > REASON_MAX_CHARS:
            problems.append(f"{judgment.requirement_id}: reason has {len(judgment.reason)} characters, more than {REASON_MAX_CHARS}")
    problems += [f"{requirement_id}: judged {count} times" for requirement_id, count in counts.items() if count > 1 and requirement_id in candidates]
    problems += [f"{requirement_id}: not judged" for requirement_id in expected if requirement_id not in counts]
    return problems


def _truncate(reason: str) -> str:
    reason = reason.strip()
    return reason if len(reason) <= REASON_MAX_CHARS else reason[: REASON_MAX_CHARS - 3].rstrip() + "..."


def finalize(
    output: JudgeOutput, requirements: Sequence, candidates: Mapping[str, Candidates], cards: Mapping[str, Mapping[str, Any]]
) -> tuple[list[Judgment], int]:
    """The final verdicts after the retry: invalid citations dropped, unsupported verdicts lowered."""
    first: dict[str, JudgmentOut] = {}
    for judgment in output.judgments:
        first.setdefault(judgment.requirement_id, judgment)
    final, dropped = [], 0
    for requirement in requirements:
        judgment = first.get(requirement.id)
        if judgment is None:
            final.append(Judgment(requirement.id, "does_not_meet", (), MISSING_REASON, downgraded=True))
            continue
        allowed = candidates[requirement.id]
        valid = tuple(
            Citation(citation.card_id, citation.quote)
            for citation in judgment.citations
            if citation_problem(citation, judgment.verdict, allowed, cards) is None
        )
        dropped += len(judgment.citations) - len(valid)
        if judgment.verdict in POSITIVE and not valid:
            final.append(Judgment(requirement.id, "does_not_meet", (), DOWNGRADED_REASON, downgraded=True))
        else:
            final.append(Judgment(requirement.id, judgment.verdict, valid, _truncate(judgment.reason)))
    return final, dropped


def read_reply(reply: ClaudeReply) -> tuple[JudgeOutput | None, list[str]]:
    if reply.stop_reason == "max_tokens":
        return None, ["the reply stopped at max_tokens before the JSON object was complete"]
    if reply.text is None:
        return None, ["the reply has no text"]
    try:
        data = parse_json_object(reply.text)
        return JudgeOutput.model_validate(data), []
    except ValueError as error:
        if isinstance(error, ValidationError):
            return None, [f"{'.'.join(str(part) for part in item['loc']) or '(root)'}: {item['msg']}" for item in error.errors()]
        return None, [str(error)]


def judge_requirements(
    requirements: Sequence,
    candidates: Sequence[Candidates],
    cards: Mapping[str, Mapping[str, Any]],
    assets: Assets,
    client: Any,
    guards: Sequence[CallGuard],
) -> JudgeResult:
    """requirements: anything with id, text_en, type and category (extracted or labeled)."""
    if not requirements:
        return JudgeResult(judgments=[])
    settings = CallSettings.from_config(assets.analysis["judge"])
    price = assets.price(assets.generation_model)
    by_id = {candidate.requirement_id: candidate for candidate in candidates}
    user = [user_content(requirements, by_id, cards)]

    reply = call_claude(client, assets.generation_model, price, assets.judge_system, user, settings, PHASE, 1, guards)
    records = [reply.record]
    output, problems = read_reply(reply)
    if output is not None:
        problems = review(output, requirements, by_id, cards)
    first_problems = list(problems)
    if problems:
        retry = call_claude(
            client,
            assets.generation_model,
            price,
            assets.judge_system,
            [*user, correction_block(problems, reply.text)],
            settings,
            PHASE,
            2,
            guards,
        )
        records.append(retry.record)
        output, errors = read_reply(retry)
        if output is None:
            raise AnalysisError(INVALID_MODEL_OUTPUT, f"{PHASE}: {'; '.join(errors[:3])}")

    judgments, dropped = finalize(output, requirements, by_id, cards)
    return JudgeResult(judgments=judgments, first_attempt_errors=first_problems, dropped_citations=dropped, records=records)
