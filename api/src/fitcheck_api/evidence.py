"""Evidence card schema: one card per file, evidence/<id>.yaml."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty")
    return value


def _lowercase(value: str) -> str:
    if value != value.lower():
        raise ValueError("must be lowercase")
    return value


NonEmpty = Annotated[str, AfterValidator(_not_blank)]
KebabId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
HttpsUrl = Annotated[str, StringConstraints(pattern=r"^https://\S+$")]
# YYYY, YYYY-MM, YYYY-MM/YYYY-MM or YYYY-MM/present.
Period = Annotated[str, StringConstraints(pattern=r"^(?:\d{4}(?:-\d{2})?|\d{4}-\d{2}/(?:\d{4}-\d{2}|present))$")]
Skill = Annotated[str, AfterValidator(_not_blank), AfterValidator(_lowercase)]

CardKind = Literal["project", "role", "skill", "profile", "education", "gap"]


class Proof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["repo", "live", "demo", "doc", "none"]
    url: HttpsUrl | None = None
    note: NonEmpty | None = None


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: KebabId
    kind: CardKind
    title: NonEmpty
    period: Period | None = None
    summary: NonEmpty
    claims: list[NonEmpty] = Field(min_length=1, max_length=8)
    skills: list[Skill] = Field(min_length=1)
    proof: list[Proof] = Field(min_length=1)

    @property
    def verifiable(self) -> bool:
        """Derived, never stored: some proof links to something a reader can check."""
        return any(proof.url for proof in self.proof)
