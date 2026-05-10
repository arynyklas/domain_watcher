"""Safety primitives for the runtime rule-suggester pipeline.

Two distinct concerns, deliberately split per ADR 0006 §7:

- **Rate limiters** (``TokenBucketLimiter``, ``PerTldLimiter``) are policy.
  They enforce ``parsing.llm_fallback.safety.max_learn_per_hour`` and
  ``max_learn_per_tld_per_24h``. They live here as ``RateLimiter``
  adapters; ``ParsingService`` injects them. The wrapper below does
  **not** rate-limit — call sites that want rate limits compose them at
  the application boundary.
- **Circuit breaker** (``SuggesterCircuitBreaker``) is transport health.
  After 5 consecutive ``SuggestionError``s in a 5-minute window, the
  breaker opens for 5 minutes; while open, calls short-circuit with
  ``SuggestionError("circuit_open", transient=True)`` without invoking
  the wrapped suggester.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, ClassVar

from domain_watcher.core.shared.errors import SuggestionError

if TYPE_CHECKING:
    from collections.abc import Callable

    from domain_watcher.core.parsing.ports import RuleSuggester
    from domain_watcher.core.parsing.value_objects import ParseRule
    from domain_watcher.core.shared.time_provider import TimeProvider
    from domain_watcher.core.shared.value_objects import DomainName


@dataclass(slots=True)
class _Bucket:
    """Token bucket state for a single key.

    ``capacity`` tokens, refill ``capacity`` tokens every ``window`` seconds.
    Sub-window decay is linear (``elapsed / window * capacity``) so a
    burst-then-cooldown pattern is rate-limited fairly without a separate
    leaky-bucket implementation.
    """

    capacity: int
    window_seconds: float
    tokens: float
    last_refill: datetime


class TokenBucketLimiter:
    """Per-host learn-call rate limit (``max_learn_per_hour``)."""

    __slots__ = ("_buckets", "_capacity", "_clock", "_lock", "_window_seconds")

    def __init__(
        self,
        *,
        capacity: int,
        window_seconds: float,
        clock: TimeProvider,
    ) -> None:
        if capacity < 1:
            raise ValueError(
                f"TokenBucketLimiter capacity must be >= 1, got {capacity}"
            )
        if window_seconds <= 0:
            raise ValueError(
                f"TokenBucketLimiter window_seconds must be > 0, got {window_seconds}"
            )
        self._capacity = capacity
        self._window_seconds = window_seconds
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> bool:
        async with self._lock:
            now = self._clock.now()
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(
                    capacity=self._capacity,
                    window_seconds=self._window_seconds,
                    tokens=float(self._capacity),
                    last_refill=now,
                )
                self._buckets[key] = bucket
            else:
                elapsed = (now - bucket.last_refill).total_seconds()
                if elapsed > 0:
                    refill = (elapsed / self._window_seconds) * self._capacity
                    bucket.tokens = min(self._capacity, bucket.tokens + refill)
                    bucket.last_refill = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False


class PerTldLimiter(TokenBucketLimiter):
    """Per-TLD learn-call rate limit (``max_learn_per_tld_per_24h``).

    Identical mechanics to ``TokenBucketLimiter``; named separately so
    callsite intent is obvious and so per-TLD defaults can diverge from
    the per-host bucket if we tighten one without the other.
    """


@dataclass(slots=True)
class CircuitBreaker:
    """N-failures-in-window circuit breaker.

    ``failures``-tracking is a deque of the last ``threshold`` failure
    timestamps; opening + closing transitions are explicit so callers
    can introspect state if they wish.
    """

    threshold: int
    failure_window: timedelta
    open_duration: timedelta
    clock: TimeProvider
    _failures: deque[datetime] = field(default_factory=deque)
    _opened_at: datetime | None = None

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self.clock.now() - self._opened_at >= self.open_duration:
            # Half-open — allow the next probe through. The caller's
            # outcome decides whether we close or re-open.
            self._opened_at = None
            self._failures.clear()
            return False
        return True

    def record_success(self) -> None:
        self._failures.clear()
        self._opened_at = None

    def record_failure(self) -> None:
        now = self.clock.now()
        self._failures.append(now)
        # Drop failures older than the failure_window.
        while self._failures and (now - self._failures[0]) > self.failure_window:
            self._failures.popleft()
        if len(self._failures) >= self.threshold:
            self._opened_at = now


@dataclass(slots=True)
class SuggesterCircuitBreaker:
    """``RuleSuggester`` wrapper that short-circuits while the breaker is open.

    NO rate limiting here — that lives in ``ParsingService`` per ADR 0006 §7.
    """

    id: ClassVar[str] = "circuit-breaker"

    inner: RuleSuggester
    breaker: CircuitBreaker

    async def suggest(self, raw_whois: str, domain: DomainName) -> ParseRule:
        if self.breaker.is_open():
            raise SuggestionError("circuit_open", transient=True)
        try:
            rule = await self.inner.suggest(raw_whois, domain)
        except SuggestionError:
            self.breaker.record_failure()
            raise
        self.breaker.record_success()
        return rule


def default_circuit_breaker(clock: TimeProvider) -> CircuitBreaker:
    """5 failures / 5 minutes opens; stays open 5 minutes (ADR 0006 §7)."""
    return CircuitBreaker(
        threshold=5,
        failure_window=timedelta(minutes=5),
        open_duration=timedelta(minutes=5),
        clock=clock,
    )


# Re-export the circuit-breaker factory; not all callers want to know
# about the timedelta values.
default_breaker_factory: Callable[[TimeProvider], CircuitBreaker] = (
    default_circuit_breaker
)


__all__ = [
    "CircuitBreaker",
    "PerTldLimiter",
    "SuggesterCircuitBreaker",
    "TokenBucketLimiter",
    "default_breaker_factory",
    "default_circuit_breaker",
]
