from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from domain_watcher.core.shared.time_provider import FixedClock, TimeProvider


def test_fixed_clock_returns_initial_time() -> None:
    at = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    clock = FixedClock(at)
    assert clock.now() == at


def test_fixed_clock_advance_by_seconds() -> None:
    at = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    clock = FixedClock(at)
    clock.advance(timedelta(seconds=30))
    assert clock.now() == at + timedelta(seconds=30)


def test_fixed_clock_advance_returns_new_now() -> None:
    at = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    clock = FixedClock(at)
    new = clock.advance(timedelta(hours=1))
    assert new == clock.now()
    assert new == at + timedelta(hours=1)


def test_fixed_clock_set_time() -> None:
    at = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    later = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)
    clock = FixedClock(at)
    clock.set(later)
    assert clock.now() == later


def test_fixed_clock_rejects_naive_datetime_at_construction() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        FixedClock(datetime(2026, 5, 9, 12, 0, 0))  # naive


def test_fixed_clock_rejects_naive_datetime_in_set() -> None:
    clock = FixedClock(datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="tz-aware"):
        clock.set(datetime(2027, 1, 1, 0, 0, 0))  # naive


def test_fixed_clock_normalizes_non_utc_to_utc() -> None:
    moscow = timezone(timedelta(hours=3))
    at = datetime(2026, 5, 9, 15, 0, 0, tzinfo=moscow)  # noon UTC
    clock = FixedClock(at)
    n = clock.now()
    assert n.tzinfo == UTC
    assert n == datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def test_fixed_clock_satisfies_protocol_runtime_check() -> None:
    clock = FixedClock(datetime(2026, 5, 9, tzinfo=UTC))
    assert isinstance(clock, TimeProvider)
