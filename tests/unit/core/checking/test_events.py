from __future__ import annotations

from datetime import UTC, datetime

from domain_watcher.core.checking.events import (
    DomainCheckCompleted,
    DomainCheckFailed,
)
from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.shared.events import DomainEvent
from domain_watcher.core.shared.value_objects import DomainName


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def test_domain_check_completed_is_domain_event() -> None:
    r = CheckResult(
        domain=DomainName("example.com"),
        outcome=CheckOutcome.OK,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        source="rdap",
    )
    e = DomainCheckCompleted(occurred_at=_now(), correlation_id="ulid", result=r)
    assert isinstance(e, DomainEvent)
    assert e.criticality == "standard"


def test_domain_check_failed_is_critical() -> None:
    e = DomainCheckFailed(
        occurred_at=_now(),
        correlation_id="ulid",
        domain=DomainName("example.com"),
        source="rdap",
        reason="timeout",
        transient=True,
    )
    assert isinstance(e, DomainEvent)
    assert e.criticality == "critical"
