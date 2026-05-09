"""Retry policy for expiration checks (ADR 0002 §3)."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain_watcher.core.shared.value_objects import Duration

_DEFAULT_BASE_DELAY: Duration = Duration(seconds=1)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Geometric backoff. ``delay_for(attempt)`` is the wait BEFORE attempt N+1.

    Defaults: 3 attempts, 1s base, factor 5.0 -> delays 1s, 5s before retries
    after attempts 1 and 2. Attempt 3 has no follow-up; asking for its delay
    is a programming error.
    """

    max_attempts: int = 3
    base_delay: Duration = field(default=_DEFAULT_BASE_DELAY)
    factor: float = 5.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.factor < 0:
            raise ValueError(f"factor must be >= 0, got {self.factor}")

    def delay_for(self, attempt: int) -> Duration:
        """Return wait before attempt N+1. Valid range: 1 .. max_attempts-1."""
        if attempt < 1:
            raise ValueError(f"attempt must be >= 1, got {attempt}")
        if attempt >= self.max_attempts:
            raise ValueError(
                f"attempt {attempt} has no follow-up (max_attempts={self.max_attempts})"
            )
        seconds = round(self.base_delay.seconds * (self.factor ** (attempt - 1)))
        return Duration.from_seconds(max(0, seconds))


__all__ = ["RetryPolicy"]
