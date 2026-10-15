"""Loads and cross-checks the evidence cards and the evaluation cases.

Used in CI (content validation, index build, retrieval eval), never in the
Lambda. Every problem is reported with its file instead of stopping at the
first one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .cases import Case
from .evidence import Card


@dataclass
class Content:
    cards: dict[str, Card] = field(default_factory=dict)
    cases: dict[str, Case] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _read_yaml(path: Path, errors: list[str]) -> Any:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        errors.append(f"{path}: invalid YAML: {error}")
        return None
    if data is None:
        errors.append(f"{path}: the file is empty")
    return data


def _validation_errors(path: Path, error: ValidationError) -> list[str]:
    messages = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"]) or "(file)"
        messages.append(f"{path}: {location}: {detail['msg']}")
    return messages


def load_cards(evidence_dir: Path, content: Content) -> None:
    for path in sorted(evidence_dir.glob("*.yaml")):
        data = _read_yaml(path, content.errors)
        if data is None:
            continue
        try:
            card = Card.model_validate(data)
        except ValidationError as error:
            content.errors.extend(_validation_errors(path, error))
            continue
        if card.id != path.stem:
            content.errors.append(f"{path}: id {card.id!r} must match the file name")
        if card.id in content.cards:
            content.errors.append(f"{path}: duplicate card id {card.id!r}")
        content.cards[card.id] = card


def load_cases(cases_dir: Path, content: Content) -> None:
    for path in sorted(cases_dir.glob("*/*.yaml")):
        data = _read_yaml(path, content.errors)
        if data is None:
            continue
        try:
            case = Case.model_validate(data)
        except ValidationError as error:
            content.errors.extend(_validation_errors(path, error))
            continue
        if case.id != path.stem:
            content.errors.append(f"{path}: id {case.id!r} must match the file name")
        if case.split != path.parent.name:
            content.errors.append(f"{path}: split {case.split!r} must match the folder {path.parent.name!r}")
        if case.id in content.cases:
            content.errors.append(f"{path}: duplicate case id {case.id!r}")
        content.cases[case.id] = case


def cross_check(content: Content, cases_dir: Path) -> None:
    gap_cards = {card_id for card_id, card in content.cards.items() if card.kind == "gap"}
    for case in content.cases.values():
        path = cases_dir / case.split / f"{case.id}.yaml"
        if case.derived_from is not None and case.derived_from not in content.cases:
            content.errors.append(f"{path}: derived_from {case.derived_from!r} is not a known case")
        for requirement in case.requirements:
            unknown = [card_id for card_id in requirement.evidence if card_id not in content.cards]
            if unknown:
                content.errors.append(f"{path}: requirements.{requirement.id}.evidence: unknown cards {unknown}")
            if requirement.verdict == "does_not_meet":
                not_gaps = [card_id for card_id in requirement.evidence if card_id in content.cards and card_id not in gap_cards]
                if not_gaps:
                    content.errors.append(
                        f"{path}: requirements.{requirement.id}.evidence: does_not_meet may cite only gap cards, got {not_gaps}"
                    )


def card_records(content: Content) -> list[dict[str, Any]]:
    """The cards as the index stores them: JSON-ready, unset optional fields left out, sorted by id."""
    return [content.cards[card_id].model_dump(mode="json", exclude_none=True) for card_id in sorted(content.cards)]


def load_content(evidence_dir: Path, cases_dir: Path | None = None) -> Content:
    """Loads the cards and, when cases_dir is given, the cases with the cross-checks."""
    content = Content()
    load_cards(evidence_dir, content)
    if cases_dir is not None:
        load_cases(cases_dir, content)
        cross_check(content, cases_dir)
    return content
