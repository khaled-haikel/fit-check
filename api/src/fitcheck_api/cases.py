"""Evaluation case schema: one case per file, evals/cases/<split>/<id>.yaml."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .evidence import KebabId, NonEmpty

Split = Literal["dev", "test", "special"]


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, StringConstraints(pattern=r"^r\d{2,}$")]
    text: NonEmpty
    text_en: NonEmpty
    type: Literal["must", "nice"]
    category: Literal["skill", "experience", "education", "language", "location", "domain", "soft"]
    verdict: Literal["meets", "partial", "does_not_meet"]
    evidence: list[KebabId] = Field(default_factory=list)
    note: NonEmpty | None = None

    @model_validator(mode="after")
    def _supported_verdicts_cite_evidence(self) -> Requirement:
        if self.verdict in ("meets", "partial") and not self.evidence:
            raise ValueError(f"a {self.verdict} verdict must cite at least one evidence card")
        return self


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: KebabId
    split: Split
    language: Literal["en", "pt"]
    source: Literal["real-condensed", "synthetic", "derived"]
    derived_from: KebabId | None = None
    special: Literal["language", "injection", "off_domain"] | None = None
    collected: date
    role_title: NonEmpty
    job_description: NonEmpty
    requirements: list[Requirement] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Case:
        if self.source == "derived" and self.derived_from is None:
            raise ValueError("a derived case must name its source in derived_from")
        if self.source != "derived" and self.derived_from is not None:
            raise ValueError("derived_from is only allowed when source is derived")
        ids = [requirement.id for requirement in self.requirements]
        duplicates = sorted({requirement_id for requirement_id in ids if ids.count(requirement_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate requirement ids: {duplicates}")
        return self
