"""MemoryMonitoredDomainRepo basics + due_for_check."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import ChannelId, CheckSchedule
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.persistence.memory import MemoryMonitoredDomainRepo

NOW = datetime(2026, 5, 9, tzinfo=UTC)


def _domain(name: str = "example.com") -> MonitoredDomain:
    return MonitoredDomain(
        name=DomainName(name),
        schedule=CheckSchedule("0 */6 * * *"),
        checker_id="rdap",
        notify_thresholds=(Duration.days(30), Duration.days(1)),
        channels=(ChannelId("tg-ops"),),
    )


async def test_add_get_remove() -> None:
    repo = MemoryMonitoredDomainRepo()
    domain = _domain()
    await repo.add(domain)
    assert await repo.get(domain.name) is not None
    await repo.remove(domain.name)
    assert await repo.get(domain.name) is None


async def test_remove_nonexistent_is_noop() -> None:
    repo = MemoryMonitoredDomainRepo()
    await repo.remove(DomainName("ghost.com"))


async def test_duplicate_add_raises() -> None:
    repo = MemoryMonitoredDomainRepo()
    domain = _domain()
    await repo.add(domain)
    with pytest.raises(ValueError):
        await repo.add(domain)


async def test_update_persists() -> None:
    repo = MemoryMonitoredDomainRepo()
    domain = _domain()
    await repo.add(domain)
    expires = NOW + timedelta(days=30)
    new = domain.with_check_result(
        CheckResult(
            domain=domain.name,
            outcome=CheckOutcome.OK,
            expires_at=expires,
            source="rdap",
        ),
        at=NOW,
    )
    await repo.update(new)
    out = await repo.get(domain.name)
    assert out is not None
    assert out.last_check is not None
    assert out.last_check.expires_at == expires


async def test_due_for_check_filters() -> None:
    repo = MemoryMonitoredDomainRepo()
    fresh = _domain("fresh.com")
    stale = _domain("stale.com")
    await repo.add(fresh)
    await repo.add(stale)
    # is_due heuristic: no last_check → due. So both are due.
    out = await repo.due_for_check(NOW)
    assert {d.name.value for d in out} == {"fresh.com", "stale.com"}
