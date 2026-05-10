"""``MonitoredDomain`` aggregate root (ADR 0002 §2)."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from domain_watcher.core.monitoring.value_objects import LastCheck

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from domain_watcher.core.checking.value_objects import CheckResult
    from domain_watcher.core.monitoring.value_objects import ChannelId, CheckSchedule
    from domain_watcher.core.shared.value_objects import DomainName, Duration


@dataclass(frozen=True, slots=True)
class MonitoredDomain:
    """Aggregate root for a single watched domain.

    Invariants (enforced in ``__post_init__``):
      - ``notify_thresholds`` non-empty and strictly descending.
      - ``channels`` non-empty.
      - ``last_check.at`` is monotonic (enforced in ``with_check_result``).
    """

    name: DomainName
    schedule: CheckSchedule
    checker_id: str
    notify_thresholds: tuple[Duration, ...]
    channels: tuple[ChannelId, ...]
    last_check: LastCheck | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.notify_thresholds:
            raise ValueError("MonitoredDomain.notify_thresholds cannot be empty")
        seconds = [d.seconds for d in self.notify_thresholds]
        for prev, cur in itertools.pairwise(seconds):
            if cur >= prev:
                raise ValueError(
                    "MonitoredDomain.notify_thresholds must be strictly descending"
                )
        if not self.channels:
            raise ValueError("MonitoredDomain.channels cannot be empty")
        if not self.checker_id:
            raise ValueError("MonitoredDomain.checker_id cannot be empty")

    def with_check_result(
        self, result: CheckResult, *, at: datetime
    ) -> MonitoredDomain:
        """Return a new instance with ``last_check`` updated.

        ``at`` is the wall-clock moment of the check (injected via TimeProvider
        at the use-case level). Going backwards relative to a previous
        last_check is rejected as a monotonic-violation programming error.
        """
        if self.last_check is not None and at < self.last_check.at:
            raise ValueError(
                "MonitoredDomain.with_check_result: last_check.at must be monotonic; "
                f"new at {at} < previous {self.last_check.at}"
            )
        new_last = LastCheck(
            at=at,
            outcome=result.outcome,
            expires_at=result.expires_at,
        )
        return MonitoredDomain(
            name=self.name,
            schedule=self.schedule,
            checker_id=self.checker_id,
            notify_thresholds=self.notify_thresholds,
            channels=self.channels,
            last_check=new_last,
            metadata=self.metadata,
        )

    def is_due(self, now: datetime) -> bool:
        """Light-weight due-ness check.

        Real scheduling happens in ``infrastructure/scheduling``; the cron
        parser is over there. For pure-core decisions we use a coarse rule:
        if there is no previous check, we are due. Otherwise we are due if
        the current time has crossed the next slot of the schedule.

        v1 keeps this simple: if more than 6 hours elapsed since the previous
        check, we are due. The cron-aware scheduler is the authoritative
        source; ``is_due`` exists for embedded callers that bypass the
        scheduler (e.g. ``DomainWatcher.check_now``).

        TODO(phase 8): replace the 6h heuristic with a cron-aware helper that
        lives in ``infrastructure/scheduling`` and is injected as a port.
        """
        if self.last_check is None:
            return True
        return now - self.last_check.at >= timedelta(hours=6)


__all__ = ["MonitoredDomain"]
