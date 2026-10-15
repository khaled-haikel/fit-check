"""Text normalization for the analysis.

Two forms, for two jobs:
- normalize_job_description: the text that is stored, hashed and sent to the
  extraction. It keeps line breaks, which carry the posting's structure.
- normalize_for_match: the form in which a requirement's source and a
  citation's quote must appear verbatim.
"""

from __future__ import annotations

import re
import unicodedata

# Curly quotes, primes, guillemets and dashes, by code point, mapped to ASCII.
_PUNCTUATION = str.maketrans(
    {chr(code): "'" for code in (0x2018, 0x2019, 0x201A, 0x201B, 0x2032)}
    | {chr(code): '"' for code in (0x201C, 0x201D, 0x201E, 0x201F, 0x2033, 0x00AB, 0x00BB)}
    | {chr(code): "-" for code in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)}
)
_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_WHITESPACE = re.compile(r"\s+")


def normalize_job_description(text: str) -> str:
    """NFKC, one kind of line break, runs of spaces collapsed, each line and the ends stripped, at most one blank line."""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def normalize_for_match(text: str) -> str:
    """NFKC, lowercase, curly quotes and dashes as ASCII, every run of whitespace (line breaks too) as one space."""
    text = unicodedata.normalize("NFKC", text).translate(_PUNCTUATION).lower()
    return _WHITESPACE.sub(" ", text).strip()


def appears_in(fragment: str, *texts: str) -> bool:
    """Whether the fragment appears verbatim, after normalize_for_match, in any of the texts."""
    needle = normalize_for_match(fragment)
    return bool(needle) and any(needle in normalize_for_match(text) for text in texts)
