from __future__ import annotations

from datetime import UTC, datetime

from domain_watcher.core.monitoring.events import (
    DomainAdded,
    DomainCheckRequested,
    DomainRemoved,
)
from domain_watcher.core.shared.events import DomainEvent
from domain_watcher.core.shared.value_objects import DomainName


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def test_domain_added() -> None:
    e = DomainAdded(
        occurred_at=_now(),
        correlation_id="ulid",
        domain=DomainName("example.com"),
    )
    assert isinstance(e, DomainEvent)
    assert e.criticality == "standard"
    assert e.domain is not None
    assert e.domain.value == "example.com"


def test_domain_removed() -> None:
    e = DomainRemoved(
        occurred_at=_now(),
        correlation_id="ulid",
        domain=DomainName("example.com"),
    )
    assert isinstance(e, DomainEvent)


def test_domain_check_requested() -> None:
    e = DomainCheckRequested(
        occurred_at=_now(),
        correlation_id="ulid",
        domain=DomainName("example.com"),
        checker_id="rdap",
    )
    assert isinstance(e, DomainEvent)
    assert e.checker_id == "rdap"
