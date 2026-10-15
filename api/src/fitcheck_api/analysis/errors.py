"""Errors that end an analysis with a known code (the API reports the code)."""

from __future__ import annotations

COST_CAP = "cost_cap"
INVALID_MODEL_OUTPUT = "invalid_model_output"
MODEL_REFUSED = "model_refused"
DEADLINE = "deadline"


class AnalysisError(Exception):
    """An expected failure: the analysis stops and is marked failed with this code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
