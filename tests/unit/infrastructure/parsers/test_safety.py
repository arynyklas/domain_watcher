"""Safety primitives: token-bucket limiters + circuit breaker.

Rate-limit policy itself (ParsingService consuming ``RateLimiter`` ports)
is exercised in ``tests/unit/application/test_parsing_service.py`` —
these tests cover the bucket/breaker mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.errors import SuggestionError
from domain_watcher.core.shared.time_provider import FixedClock
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.parsers.safety import (
    CircuitBreaker,
    PerTldLimiter,
    SuggesterCircuitBreaker,
    TokenBucketLimiter,
    default_circuit_breaker,
)

NOW = datetime(2026, 5, 9, 0, 0, 0, tzinfo=UTC)


# Rate limiters --------------------------------------------------------------


async def test_token_bucket_allows_capacity_then_rejects() -> None:
    clock = FixedClock(NOW)
    limiter = TokenBucketLimiter(capacity=3, window_seconds=3600, clock=clock)
    assert await limiter.acquire("host") is True
    assert await limiter.acquire("host") is True
    assert await limiter.acquire("host") is True
    assert await limiter.acquire("host") is False


async def test_token_bucket_refills_over_time() -> None:
    clock = FixedClock(NOW)
    limiter = TokenBucketLimiter(capacity=2, window_seconds=3600, clock=clock)
    assert await limiter.acquire("h") is True
    assert await limiter.acquire("h") is True
    assert await limiter.acquire("h") is False
    # Half the window → +1 token (capacity=2, half window = 1 refill).
    clock.advance(timedelta(seconds=1800))
    assert await limiter.acquire("h") is True
    assert await limiter.acquire("h") is False


async def test_token_bucket_keys_are_independent() -> None:
    clock = FixedClock(NOW)
    limiter = TokenBucketLimiter(capacity=1, window_seconds=3600, clock=clock)
    assert await limiter.acquire("a") is True
    assert await limiter.acquire("b") is True
    assert await limiter.acquire("a") is False
    assert await limiter.acquire("b") is False


async def test_per_tld_limiter_inherits_bucket_semantics() -> None:
    clock = FixedClock(NOW)
    limiter = PerTldLimiter(capacity=3, window_seconds=86400, clock=clock)
    for _ in range(3):
        assert await limiter.acquire("com") is True
    assert await limiter.acquire("com") is False


def test_token_bucket_validates_constructor_args() -> None:
    clock = FixedClock(NOW)
    with pytest.raises(ValueError, match="capacity"):
        TokenBucketLimiter(capacity=0, window_seconds=3600, clock=clock)
    with pytest.raises(ValueError, match="window_seconds"):
        TokenBucketLimiter(capacity=1, window_seconds=0, clock=clock)


# Circuit breaker ------------------------------------------------------------


def test_breaker_opens_after_threshold_failures_in_window() -> None:
    clock = FixedClock(NOW)
    breaker = CircuitBreaker(
        threshold=5,
        failure_window=timedelta(minutes=5),
        open_duration=timedelta(minutes=5),
        clock=clock,
    )
    assert not breaker.is_open()
    for _ in range(4):
        breaker.record_failure()
    assert not breaker.is_open()
    breaker.record_failure()  # 5th — opens
    assert breaker.is_open()


def test_breaker_drops_failures_older_than_window() -> None:
    clock = FixedClock(NOW)
    breaker = CircuitBreaker(
        threshold=5,
        failure_window=timedelta(minutes=5),
        open_duration=timedelta(minutes=5),
        clock=clock,
    )
    for _ in range(4):
        breaker.record_failure()
    clock.advance(timedelta(minutes=10))  # all four are now stale
    breaker.record_failure()
    assert not breaker.is_open()


def test_breaker_closes_after_open_duration() -> None:
    clock = FixedClock(NOW)
    breaker = CircuitBreaker(
        threshold=2,
        failure_window=timedelta(minutes=5),
        open_duration=timedelta(minutes=5),
        clock=clock,
    )
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open()
    clock.advance(timedelta(minutes=6))
    assert not breaker.is_open()  # half-open / closed


def test_breaker_success_clears_failures() -> None:
    clock = FixedClock(NOW)
    breaker = CircuitBreaker(
        threshold=3,
        failure_window=timedelta(minutes=5),
        open_duration=timedelta(minutes=5),
        clock=clock,
    )
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert not breaker.is_open()


def test_default_breaker_matches_adr_constants() -> None:
    clock = FixedClock(NOW)
    breaker = default_circuit_breaker(clock)
    assert breaker.threshold == 5
    assert breaker.failure_window == timedelta(minutes=5)
    assert breaker.open_duration == timedelta(minutes=5)


# SuggesterCircuitBreaker ----------------------------------------------------


@dataclass
class StubSuggester:
    id: ClassVar[str] = "stub"
    rule: ParseRule | None = None
    raises: BaseException | None = None
    calls: int = 0
    last_args: tuple[str, DomainName] | None = field(default=None)

    async def suggest(self, raw_whois: str, domain: DomainName) -> ParseRule:
        self.calls += 1
        self.last_args = (raw_whois, domain)
        if self.raises is not None:
            raise self.raises
        if self.rule is None:
            raise AssertionError("StubSuggester.rule unset")
        return self.rule


_GOOD_RULE = ParseRule(
    tld="com",
    expires_regex=RegexPattern(r"E:\s*(\S+)"),
    date_format=DateFormat.ISO_8601,
)


async def test_wrapper_passes_through_when_breaker_closed() -> None:
    clock = FixedClock(NOW)
    inner = StubSuggester(rule=_GOOD_RULE)
    wrapper = SuggesterCircuitBreaker(
        inner=inner, breaker=default_circuit_breaker(clock)
    )
    out = await wrapper.suggest("E: 2030-01-01", DomainName("example.com"))
    assert out == _GOOD_RULE
    assert inner.calls == 1


async def test_wrapper_records_failure_and_short_circuits() -> None:
    clock = FixedClock(NOW)
    inner = StubSuggester(raises=SuggestionError("flake", transient=True))
    breaker = CircuitBreaker(
        threshold=2,
        failure_window=timedelta(minutes=5),
        open_duration=timedelta(minutes=5),
        clock=clock,
    )
    wrapper = SuggesterCircuitBreaker(inner=inner, breaker=breaker)
    for _ in range(2):
        with pytest.raises(SuggestionError):
            await wrapper.suggest("x", DomainName("example.com"))
    # Now breaker is open → next call MUST short-circuit (no inner.calls increase).
    calls_at_open = inner.calls
    with pytest.raises(SuggestionError) as exc_info:
        await wrapper.suggest("x", DomainName("example.com"))
    assert "circuit_open" in str(exc_info.value)
    assert exc_info.value.transient is True
    assert inner.calls == calls_at_open  # short-circuited


async def test_wrapper_does_not_rate_limit() -> None:
    """Per ADR 0006 §7 the wrapper is transport health only."""
    clock = FixedClock(NOW)
    inner = StubSuggester(rule=_GOOD_RULE)
    wrapper = SuggesterCircuitBreaker(
        inner=inner, breaker=default_circuit_breaker(clock)
    )
    # Hammer it: no rate-limit rejection should ever occur.
    for _ in range(50):
        await wrapper.suggest("E: 2030-01-01", DomainName("example.com"))
    assert inner.calls == 50
