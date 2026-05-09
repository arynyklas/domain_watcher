"""``NotificationPolicy`` — pure threshold-crossing decision (ADR 0002 §5).

The policy decides *what alerts a transition warrants*. It does NOT consult
any IdempotencyStore — that check happens at dispatch time, keyed by
``(domain, threshold, cycle_id, channel)``. A renewal yields a new
``cycle_id`` so the same threshold re-fires for the new cycle.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.notification.entities import Alert, AlertSeverity
from domain_watcher.core.shared.value_objects import Duration

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from domain_watcher.core.checking.value_objects import CheckResult
    from domain_watcher.core.monitoring.value_objects import LastCheck


def _cycle_id(expires_at: datetime) -> str:
    """Stable per-cycle id: sha256 of the ISO expiration timestamp, truncated."""
    return hashlib.sha256(expires_at.isoformat().encode()).hexdigest()[:16]


def _severity_for(threshold: Duration) -> AlertSeverity:
    """Map a threshold to a default severity.

    Defaults from ADR 0002 §5 / plan: 30d→INFO, 7d→WARNING, 1d→CRITICAL.
    Anything tighter than 1d is also CRITICAL; wider than 7d is INFO.
    """
    if threshold <= Duration.days(1):
        return AlertSeverity.CRITICAL
    if threshold <= Duration.days(7):
        return AlertSeverity.WARNING
    return AlertSeverity.INFO


@dataclass(frozen=True, slots=True)
class NotificationPolicy:
    """Decides whether a check transition crosses any thresholds."""

    thresholds: tuple[Duration, ...]

    def __post_init__(self) -> None:
        if not self.thresholds:
            raise ValueError("NotificationPolicy.thresholds cannot be empty")
        seconds = [t.seconds for t in self.thresholds]
        for prev, cur in itertools.pairwise(seconds):
            if cur >= prev:
                raise ValueError("NotificationPolicy.thresholds must be strictly descending")

    def alerts_for(
        self,
        previous: LastCheck | None,
        current: CheckResult,
        now: datetime,
    ) -> Sequence[Alert]:
        """Return alerts to dispatch for this transition.

        Pure: same inputs always yield the same output. Caller deduplicates
        against the ``IdempotencyStore`` at dispatch time.
        """
        if current.outcome is not CheckOutcome.OK or current.expires_at is None:
            return ()

        time_left = current.expires_at - now
        cycle = _cycle_id(current.expires_at)

        # Same-cycle prev allows us to suppress already-crossed thresholds.
        same_cycle_prev = (
            previous is not None
            and previous.outcome is CheckOutcome.OK
            and previous.expires_at is not None
            and previous.expires_at == current.expires_at
        )
        if same_cycle_prev:
            assert previous is not None
            assert previous.expires_at is not None
            prev_time_left = previous.expires_at - previous.at
        else:
            prev_time_left = None

        alerts: list[Alert] = []
        for threshold in self.thresholds:
            crossed_now = time_left <= threshold.as_timedelta()
            if not crossed_now:
                continue
            crossed_before = (
                prev_time_left is not None and prev_time_left <= threshold.as_timedelta()
            )
            if crossed_before:
                continue
            alerts.append(
                Alert(
                    domain=current.domain,
                    expires_at=current.expires_at,
                    threshold=threshold,
                    severity=_severity_for(threshold),
                    cycle_id=cycle,
                )
            )
        return tuple(alerts)


__all__ = ["NotificationPolicy"]
