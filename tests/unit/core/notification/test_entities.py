from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.notification.entities import (
    Alert,
    AlertSeverity,
    Channel,
)
from domain_watcher.core.shared.value_objects import DomainName, Duration


def test_alert_severity_values() -> None:
    assert AlertSeverity.INFO == "info"
    assert AlertSeverity.WARNING == "warning"
    assert AlertSeverity.CRITICAL == "critical"


def test_channel_basic() -> None:
    c = Channel(id=ChannelId("tg-ops"), notifier_id="telegram")
    assert c.id.value == "tg-ops"
    assert c.notifier_id == "telegram"
    assert dict(c.routing) == {}


def test_channel_with_routing() -> None:
    c = Channel(
        id=ChannelId("tg-user-42"),
        notifier_id="telegram",
        routing={"chat_id": "12345"},
    )
    assert c.routing["chat_id"] == "12345"


def test_channel_empty_notifier_id_rejected() -> None:
    with pytest.raises(ValueError, match="notifier_id"):
        Channel(id=ChannelId("tg-ops"), notifier_id="")


def test_alert_basic() -> None:
    a = Alert(
        domain=DomainName("example.com"),
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        threshold=Duration.days(30),
        severity=AlertSeverity.INFO,
        cycle_id="a" * 16,
    )
    assert a.domain.value == "example.com"
    assert len(a.cycle_id) == 16


def test_alert_naive_expires_at_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        Alert(
            domain=DomainName("example.com"),
            expires_at=datetime(2027, 1, 1),
            threshold=Duration.days(30),
            severity=AlertSeverity.INFO,
            cycle_id="a" * 16,
        )


def test_alert_bad_cycle_id_rejected() -> None:
    with pytest.raises(ValueError, match="cycle_id"):
        Alert(
            domain=DomainName("example.com"),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            threshold=Duration.days(30),
            severity=AlertSeverity.INFO,
            cycle_id="not-16-hex",
        )
