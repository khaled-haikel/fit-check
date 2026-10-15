"""Model prices, call costs and the spending guards checked before every model call."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, TypeVar

from .errors import COST_CAP, AnalysisError

T = TypeVar("T")
MILLION = Decimal(1_000_000)
# Worst case before a call: one token for every 3 characters of input.
CHARS_PER_TOKEN = 3


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens (config/prices.json)."""

    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_read: Decimal


def load_prices(data: Mapping[str, Mapping[str, Any]]) -> dict[str, ModelPrice]:
    return {
        model: ModelPrice(*(Decimal(str(price[field])) for field in ("input", "output", "cache_write", "cache_read")))
        for model, price in data.items()
    }


def usage_cost(price: ModelPrice, input_tokens: int, output_tokens: int, cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> Decimal:
    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_write_tokens * price.cache_write
        + cache_read_tokens * price.cache_read
    ) / MILLION


def worst_case_cost(price: ModelPrice, input_chars: int, max_output_tokens: int) -> Decimal:
    """The most a call can cost: every input token at the dearer of input and cache write, max_tokens of output."""
    input_tokens = math.ceil(input_chars / CHARS_PER_TOKEN)
    return (input_tokens * max(price.input, price.cache_write) + max_output_tokens * price.output) / MILLION


def to_micro_usd(amount: Decimal) -> int:
    """Whole micro-dollars, rounded up; DynamoDB never stores a float."""
    return math.ceil(amount * MILLION)


@dataclass(frozen=True)
class CallRecord:
    """One model call, as logged and summed."""

    phase: str
    model: str
    attempt: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    latency_ms: float
    stop_reason: str | None
    request_id: str
    retry_attempts: int
    cached: bool
    # From the usage and config/prices.json, whether or not the response came from a cache.
    cost_usd: Decimal

    def log_fields(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "model": self.model,
            "attempt": self.attempt,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "latency_ms": round(self.latency_ms, 1),
            "stop_reason": self.stop_reason,
            "request_id": self.request_id,
            "retry_attempts": self.retry_attempts,
            "cached": self.cached,
            "cost_usd": float(round(self.cost_usd, 6)),
        }


class CallGuard(Protocol):
    """Checked around every model call; raising in before_call stops the call."""

    def before_call(self, phase: str, worst_case_usd: Decimal, cached: bool) -> None: ...

    def after_call(self, record: CallRecord, worst_case_usd: Decimal) -> None: ...

    def call_failed(self, worst_case_usd: Decimal, cached: bool) -> None: ...


def guarded_call(guards: Sequence[CallGuard], phase: str, worst_case_usd: Decimal, cached: bool, call: Callable[[], T], record_of: Callable[[T], CallRecord]) -> T:
    """Runs one model call between the guards' checks; a guard that raises stops it before it starts."""
    passed: list[CallGuard] = []
    try:
        for guard in guards:
            guard.before_call(phase, worst_case_usd, cached)
            passed.append(guard)
        result = call()
    except Exception:
        for guard in passed:
            guard.call_failed(worst_case_usd, cached)
        raise
    for guard in guards:
        guard.after_call(record_of(result), worst_case_usd)
    return result


class AnalysisBudget:
    """The per-analysis cap: spent so far plus the next call's worst case must stay within it.

    It counts the usage-based cost even for cached responses, so a replayed
    analysis ends exactly as the live one would.
    """

    def __init__(self, cap_usd: Decimal, spent_usd: Decimal = Decimal(0)) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd

    def before_call(self, phase: str, worst_case_usd: Decimal, cached: bool) -> None:
        if self.spent_usd + worst_case_usd > self.cap_usd:
            raise AnalysisError(
                COST_CAP,
                f"{phase}: spent {self.spent_usd:.4f} + worst case {worst_case_usd:.4f} > cap {self.cap_usd} USD",
            )

    def after_call(self, record: CallRecord, worst_case_usd: Decimal) -> None:
        self.spent_usd += record.cost_usd

    def call_failed(self, worst_case_usd: Decimal, cached: bool) -> None:
        """A call that raised returned no usage; Bedrock bills nothing for it."""


class RunBudget:
    """A cap shared by concurrent analyses (the evaluation's --max-cost-usd).

    Each call reserves its worst case under a lock before it starts and swaps
    it for the actual cost when it ends, so two calls in flight cannot jointly
    overshoot. Responses from a cache cost nothing.
    """

    def __init__(self, cap_usd: Decimal) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = Decimal(0)
        self.reserved_usd = Decimal(0)
        self._lock = threading.Lock()

    def before_call(self, phase: str, worst_case_usd: Decimal, cached: bool) -> None:
        if cached:
            return
        with self._lock:
            if self.spent_usd + self.reserved_usd + worst_case_usd > self.cap_usd:
                raise RunCapReached(phase, self.spent_usd, self.reserved_usd, worst_case_usd, self.cap_usd)
            self.reserved_usd += worst_case_usd

    def after_call(self, record: CallRecord, worst_case_usd: Decimal) -> None:
        if record.cached:
            return
        with self._lock:
            self.reserved_usd -= worst_case_usd
            self.spent_usd += record.cost_usd

    def call_failed(self, worst_case_usd: Decimal, cached: bool) -> None:
        """Drops the reservation of a call that raised before returning a usage."""
        if cached:
            return
        with self._lock:
            self.reserved_usd -= worst_case_usd


class RunCapReached(Exception):
    def __init__(self, phase: str, spent: Decimal, reserved: Decimal, worst: Decimal, cap: Decimal) -> None:
        super().__init__(
            f"run cost cap reached before a {phase} call: spent {spent:.4f} + in flight {reserved:.4f} "
            f"+ worst case {worst:.4f} > {cap} USD"
        )
