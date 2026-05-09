"""SchedulerService Protocol + MemoryScheduler reconcile contract."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain_watcher.application.scheduling import MemoryScheduler, SchedulerService
from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import (
    ChannelId,
    CheckSchedule,
    LastCheck,
)
from domain_watcher.core.shared.value_objects import DomainName, Duration


def _domain(name: str, cron: str = "0 */6 * * *") -> MonitoredDomain:
    return MonitoredDomain(
        name=DomainName(name),
        schedule=CheckSchedule(cron),
        checker_id="rdap",
        notify_thresholds=(Duration.days(30), Duration.days(7), Duration.days(1)),
        channels=(ChannelId("tg-ops"),),
        last_check=None,
    )


async def test_protocol_isinstance() -> None:
    assert isinstance(MemoryScheduler(), SchedulerService)


async def test_add_remove_list_jobs() -> None:
    s = MemoryScheduler()

    async def fn() -> None:
        return None

    await s.add_job(DomainName("a.com"), "0 0 * * *", fn)
    await s.add_job(DomainName("b.com"), "0 1 * * *", fn)
    assert set(s.list_jobs()) == {"check:a.com", "check:b.com"}
    await s.remove_job(DomainName("a.com"))
    assert set(s.list_jobs()) == {"check:b.com"}


async def test_duplicate_add_rejected() -> None:
    s = MemoryScheduler()

    async def fn() -> None:
        return None

    await s.add_job(DomainName("a.com"), "0 0 * * *", fn)
    with pytest.raises(ValueError):
        await s.add_job(DomainName("a.com"), "0 1 * * *", fn)


async def test_add_or_update_idempotent() -> None:
    s = MemoryScheduler()

    async def fn() -> None:
        return None

    await s.add_or_update_job(DomainName("a.com"), "0 0 * * *", fn)
    await s.add_or_update_job(DomainName("a.com"), "0 0 * * *", fn)
    assert s.list_jobs() == ("check:a.com",)


async def test_reconcile_unchanged_changed_added_removed() -> None:
    s = MemoryScheduler()

    async def fn() -> None:
        return None

    keep = _domain("keep.com", "0 0 * * *")
    change = _domain("change.com", "0 0 * * *")
    await s.add_or_update_job(keep.name, keep.schedule.cron, fn)
    await s.add_or_update_job(change.name, change.schedule.cron, fn)
    await s.add_or_update_job(DomainName("gone.com"), "0 0 * * *", fn)

    new_change = _domain("change.com", "0 12 * * *")
    added = _domain("new.com", "0 6 * * *")

    def factory(d: MonitoredDomain):
        async def _job() -> None:
            return None

        return _job

    await s.reconcile([keep, new_change, added], callable_factory=factory)
    assert set(s.list_jobs()) == {"check:keep.com", "check:change.com", "check:new.com"}
    # The cron for change.com was updated
    assert s.jobs["check:change.com"][0] == "0 12 * * *"


async def test_start_stop_idempotent() -> None:
    s = MemoryScheduler()
    await s.start()
    await s.start()
    assert s.started is True
    await s.stop()
    await s.stop()
    assert s.started is False


async def test_trigger_invokes_callable() -> None:
    s = MemoryScheduler()
    called: list[int] = []

    async def fn() -> None:
        called.append(1)

    await s.add_or_update_job(DomainName("a.com"), "0 0 * * *", fn)
    await s.trigger(DomainName("a.com"))
    assert called == [1]


# These imports prevent unused-warning by being referenced via type hints
_ = (CheckOutcome, LastCheck, datetime, UTC)
