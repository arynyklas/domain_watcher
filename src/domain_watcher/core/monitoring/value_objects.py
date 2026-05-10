"""Value objects for the monitoring bounded context (ADR 0002 §2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain_watcher.core.checking.value_objects import CheckOutcome

if TYPE_CHECKING:
    from datetime import datetime

# A 5-field cron expression: minute hour day-of-month month day-of-week.
_CRON_FIELD_COUNT = 5


@dataclass(frozen=True, slots=True)
class ChannelId:
    """Stable identifier for a notification channel (e.g. ``tg-ops``)."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(
                f"ChannelId.value must be str, got {type(self.value).__name__}"
            )
        v = self.value.strip()
        if not v:
            raise ValueError("ChannelId cannot be empty")
        object.__setattr__(self, "value", v)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CheckSchedule:
    """A 5-field cron expression. The actual cron parser lives in the scheduler
    adapter (``infrastructure/scheduling``); core only validates structure."""

    cron: str

    def __post_init__(self) -> None:
        if not isinstance(self.cron, str):
            raise TypeError("CheckSchedule.cron must be str")
        if not self.cron.strip():
            raise ValueError("CheckSchedule.cron cannot be empty")
        # Lightweight structural validation: 5 whitespace-separated fields.
        # Real cron parsing happens in apscheduler.
        fields = self.cron.split()
        if len(fields) != _CRON_FIELD_COUNT:
            raise ValueError(
                f"CheckSchedule.cron must have {_CRON_FIELD_COUNT} "
                f"whitespace-separated fields, got {len(fields)}: {self.cron!r}"
            )


@dataclass(frozen=True, slots=True)
class LastCheck:
    """Snapshot of the last completed check for a domain."""

    at: datetime
    outcome: CheckOutcome
    expires_at: datetime | None

    def __post_init__(self) -> None:
        ok = self.outcome is CheckOutcome.OK
        has_date = self.expires_at is not None
        if ok != has_date:
            raise ValueError(
                "LastCheck invariant: outcome == OK ⇔ expires_at is not None"
            )
        if self.at.tzinfo is None or self.at.tzinfo.utcoffset(self.at) is None:
            raise ValueError("LastCheck.at must be tz-aware UTC")
        if self.expires_at is not None:
            tz = self.expires_at.tzinfo
            if tz is None or tz.utcoffset(self.expires_at) is None:
                raise ValueError("LastCheck.expires_at must be tz-aware UTC")


__all__ = ["ChannelId", "CheckSchedule", "LastCheck"]
