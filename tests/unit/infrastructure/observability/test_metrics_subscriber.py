"""Tests for the metrics event-bus subscriber.

Verifies that publishing the relevant events on an in-process bus ticks
the matching Prometheus counters. The metric values are read out of the
shared registry — counters never decrease, so we record the before /
after delta rather than asserting an absolute value.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from prometheus_client import generate_latest

from domain_watcher.application import metrics_subscriber
from domain_watcher.application.event_bus import InProcessEventBus
from domain_watcher.core.checking.events import (
    DomainCheckCompleted,
    DomainCheckFailed,
)
from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.notification.entities import Alert, AlertSeverity
from domain_watcher.core.notification.events import NotificationDispatched
from domain_watcher.core.parsing.events import (
    WhoisRuleInvalidated,
    WhoisRuleLearned,
)
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.observability.metrics import REGISTRY


def _read(name: str, **labels: str) -> float:
    body = generate_latest(REGISTRY).decode()
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    needle = f"{name}{{{label_str}}} "
    for line in body.splitlines():
        if line.startswith(needle):
            return float(line.removeprefix(needle))
    return 0.0


async def _drain(bus: InProcessEventBus) -> None:
    """Yield to the loop a few times so callback dispatch completes."""

    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_check_completed_increments_counter() -> None:
    bus = InProcessEventBus()
    metrics_subscriber.register(bus)

    before = _read("domain_watcher_checks_total", checker="rdap", outcome="ok")
    result = CheckResult(
        domain=DomainName("ex.com"),
        outcome=CheckOutcome.OK,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        source="rdap",
    )
    await bus.publish(
        DomainCheckCompleted(occurred_at=datetime(2026, 5, 9, tzinfo=UTC), result=result)
    )
    await _drain(bus)
    after = _read("domain_watcher_checks_total", checker="rdap", outcome="ok")

    assert after == before + 1


@pytest.mark.asyncio
async def test_check_failed_uses_transient_outcome_label() -> None:
    bus = InProcessEventBus()
    metrics_subscriber.register(bus)

    before = _read("domain_watcher_checks_total", checker="whois", outcome="transient_error")
    await bus.publish(
        DomainCheckFailed(
            occurred_at=datetime(2026, 5, 9, tzinfo=UTC),
            domain=DomainName("ex.com"),
            source="whois",
            reason="timeout",
            transient=True,
        )
    )
    await _drain(bus)
    after = _read("domain_watcher_checks_total", checker="whois", outcome="transient_error")

    assert after == before + 1


@pytest.mark.asyncio
async def test_notification_dispatched_increments_alerts_sent() -> None:
    bus = InProcessEventBus()
    metrics_subscriber.register(bus)

    alert = Alert(
        domain=DomainName("ex.com"),
        threshold=Duration.days(7),
        cycle_id="0123456789abcdef",
        severity=AlertSeverity.WARNING,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    before = _read("domain_watcher_alerts_sent_total", channel="tg-ops", severity="warning")
    await bus.publish(
        NotificationDispatched(
            occurred_at=datetime(2026, 5, 9, tzinfo=UTC),
            alert=alert,
            channel=ChannelId("tg-ops"),
        )
    )
    await _drain(bus)
    after = _read("domain_watcher_alerts_sent_total", channel="tg-ops", severity="warning")

    assert after == before + 1


@pytest.mark.asyncio
async def test_rule_learned_and_invalidated_counters() -> None:
    bus = InProcessEventBus()
    metrics_subscriber.register(bus)

    before_learned = _read("domain_watcher_rules_learned_total", suggester="litellm", tld="ai")
    before_invalidated = _read(
        "domain_watcher_rules_invalidated_total", reason="cross_check", tld="ai"
    )

    await bus.publish(
        WhoisRuleLearned(
            occurred_at=datetime(2026, 5, 9, tzinfo=UTC),
            tld="ai",
            suggester_id="litellm",
            rule_id=1,
            sample_domain=DomainName("foo.ai"),
        )
    )
    await bus.publish(
        WhoisRuleInvalidated(
            occurred_at=datetime(2026, 5, 9, tzinfo=UTC),
            tld="ai",
            rule_id=1,
            reason="cross_check",
        )
    )
    await _drain(bus)

    assert (
        _read("domain_watcher_rules_learned_total", suggester="litellm", tld="ai")
        == before_learned + 1
    )
    assert (
        _read("domain_watcher_rules_invalidated_total", reason="cross_check", tld="ai")
        == before_invalidated + 1
    )
