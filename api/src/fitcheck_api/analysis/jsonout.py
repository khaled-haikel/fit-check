"""Reading the JSON object a model writes in its text, as the prompts ask (no structured output)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_JSON_FENCE = re.compile(r"```json[ \t]*\n(.*?)\n?```", re.DOTALL | re.IGNORECASE)


def last_text_block(content: Sequence[Mapping[str, Any]]) -> str:
    """The text of the last text block; thinking blocks come before it and are ignored."""
    texts = [block.get("text", "") for block in content if block.get("type") == "text"]
    if not texts:
        raise ValueError("the response has no text block")
    return texts[-1]


def parse_json_object(text: str) -> dict[str, Any]:
    """The JSON object in the text: the whole text, or the last ```json block when there is one."""
    fenced = _JSON_FENCE.findall(text)
    candidate = fenced[-1] if fenced else text
    try:
        value = json.loads(candidate.strip())
    except json.JSONDecodeError as error:
        raise ValueError(f"the response is not valid JSON ({error.msg} at line {error.lineno}, column {error.colno})") from None
    if not isinstance(value, dict):
        raise ValueError("the response JSON is not an object")
    return value
