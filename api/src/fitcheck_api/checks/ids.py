"""Identifiers and keys of the checks table (docs/adr/0006-async-analysis.md)."""

from __future__ import annotations

import base64
import hashlib
import re
import time

# base32 lowercase: letters and the digits 2 to 7.
CHECK_ID = re.compile(r"^[a-z2-7]{26}$")


def check_id(analysis_version: str, job_description: str) -> str:
    """The same job description under the same analysis_version gets the same ID: that is the cache."""
    digest = hashlib.sha256(f"{analysis_version}\n{job_description}".encode("utf-8")).digest()
    return base64.b32encode(digest).decode("ascii").lower()[:26]


def ip_hash(ip: str) -> str:
    """The client IP is never stored or logged; only this prefix of its sha256."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


def text_hash(text: str) -> str:
    """What the logs carry instead of a job description."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def day_key(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def month_key(now: float) -> str:
    return time.strftime("%Y-%m", time.gmtime(now))


def iso(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def check_key(identifier: str) -> str:
    return f"check#{identifier}"


def rate_key(client_hash: str, day: str) -> str:
    return f"rate#{client_hash}#{day}"


def spend_key(month: str) -> str:
    return f"spend#{month}"
