"""``TimeProvider`` port and the in-test ``FixedClock`` helper.

``core/`` never calls ``datetime.utcnow()``. Use the injected port — that
makes time-dependent logic testable and keeps every datetime tz-aware UTC.

``FixedClock`` lives here (not in ``infrastructure/``) because it is a pure
helper with no I/O. It is part of ``core/shared`` so that core/application
unit tests can import it without crossing layer boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class TimeProvider(Protocol):
    """Returns the current instant as a tz-aware UTC ``datetime``."""

    def now(self) -> datetime: ...


def _ensure_utc(at: datetime) -> datetime:
    if at.tzinfo is None or at.tzinfo.utcoffset(at) is None:
        raise ValueError("FixedClock requires tz-aware datetime")
    return at.astimezone(UTC)


class FixedClock:
    """Deterministic clock for tests. Mutable, but not shared — one per test."""

    __slots__ = ("_now",)

    def __init__(self, at: datetime) -> None:
        self._now = _ensure_utc(at)

    def now(self) -> datetime:
        return self._now

    def set(self, at: datetime) -> datetime:
        """Replace current time. Returns new now."""
        self._now = _ensure_utc(at)
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        """Move forward by ``delta``. Returns new now."""
        self._now = self._now + delta
        return self._now


__all__ = ["FixedClock", "TimeProvider"]
