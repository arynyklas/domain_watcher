from __future__ import annotations

from datetime import UTC, datetime

from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.notification.entities import Alert, AlertSeverity
from domain_watcher.core.notification.events import (
    NotificationDispatched,
    NotificationFailed,
)
from domain_watcher.core.shared.events import DomainEvent
from domain_watcher.core.shared.value_objects import DomainName, Duration


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def _alert() -> Alert:
    return Alert(
        domain=DomainName("example.com"),
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        threshold=Duration.days(30),
        severity=AlertSeverity.INFO,
        cycle_id="a" * 16,
    )


def test_notification_dispatched() -> None:
    e = NotificationDispatched(
        occurred_at=_now(),
        correlation_id="ulid",
        alert=_alert(),
        channel=ChannelId("tg-ops"),
    )
    assert isinstance(e, DomainEvent)
    assert e.criticality == "standard"


def test_notification_failed_is_critical() -> None:
    e = NotificationFailed(
        occurred_at=_now(),
        correlation_id="ulid",
        alert=_alert(),
        channel=ChannelId("tg-ops"),
        reason="429 too many requests",
        attempts=3,
    )
    assert e.criticality == "critical"
