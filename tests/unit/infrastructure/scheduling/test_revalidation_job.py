"""Periodic revalidation job wiring on top of ApsScheduler (Task 8.2)."""

from __future__ import annotations

import asyncio

import pytest

from domain_watcher.core.shared.value_objects import Duration
from domain_watcher.infrastructure.scheduling.apscheduler import ApsScheduler


async def _noop() -> None:
    return None


async def test_add_revalidation_job_schedules_interval_trigger() -> None:
    sched = ApsScheduler()
    await sched.add_revalidation_job(Duration.days(1), _noop)
    assert "revalidate-learned-rules" in sched.list_jobs()
    job = sched._scheduler.get_job("revalidate-learned-rules")
    assert job is not None
    # IntervalTrigger str includes the configured interval.
    assert "1 day" in str(job.trigger) or "86400" in str(job.trigger)
    await sched.stop()


async def test_add_revalidation_job_rejects_non_positive_interval() -> None:
    sched = ApsScheduler()
    with pytest.raises(ValueError):
        await sched.add_revalidation_job(Duration.from_seconds(0), _noop)
    await sched.stop()


async def test_add_revalidation_job_replaces_existing() -> None:
    sched = ApsScheduler()
    await sched.add_revalidation_job(Duration.days(1), _noop)
    await sched.add_revalidation_job(Duration.hours(6), _noop)
    job = sched._scheduler.get_job("revalidate-learned-rules")
    assert "21600" in str(job.trigger) or "6:00:00" in str(job.trigger)
    # Still exactly one revalidation job.
    assert sched.list_jobs().count("revalidate-learned-rules") == 1
    await sched.stop()


async def test_revalidation_job_not_touched_by_domain_reconcile() -> None:
    """``reconcile`` over per-domain MonitoredDomains MUST leave the
    revalidation job alone."""
    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.monitoring.value_objects import ChannelId, CheckSchedule
    from domain_watcher.core.shared.value_objects import DomainName

    sched = ApsScheduler()
    await sched.add_revalidation_job(Duration.days(1), _noop)
    await sched.reconcile(
        [
            MonitoredDomain(
                name=DomainName("a.test"),
                schedule=CheckSchedule(cron="0 */6 * * *"),
                checker_id="rdap",
                notify_thresholds=(
                    Duration.days(30),
                    Duration.days(7),
                    Duration.days(1),
                ),
                channels=(ChannelId("tg-ops"),),
            ),
        ],
        callable_factory=lambda _d: _noop,
    )
    assert "revalidate-learned-rules" in sched.list_jobs()
    assert "check:a.test" in sched.list_jobs()

    # Now reconcile to an empty domain set — revalidation must survive.
    await sched.reconcile([], callable_factory=lambda _d: _noop)
    assert sched.list_jobs() == ("revalidate-learned-rules",)
    await sched.stop()


async def test_remove_revalidation_job() -> None:
    sched = ApsScheduler()
    await sched.add_revalidation_job(Duration.days(1), _noop)
    await sched.remove_revalidation_job()
    assert sched.list_jobs() == ()
    # Idempotent.
    await sched.remove_revalidation_job()
    await sched.stop()


async def test_revalidation_job_actually_fires() -> None:
    """Smoke test: a fast-interval revalidation job runs the callable."""
    sched = ApsScheduler()
    fired = asyncio.Event()

    async def _job() -> None:
        fired.set()

    # Smallest valid interval (1s); we trigger early via APScheduler internals.
    await sched.add_revalidation_job(Duration.from_seconds(1), _job)
    await sched.start()
    try:
        # Force the next run to be ~now so we don't wait the full second.
        from datetime import UTC, datetime, timedelta

        sched._scheduler.modify_job(
            "revalidate-learned-rules",
            next_run_time=datetime.now(tz=UTC) + timedelta(milliseconds=10),
        )
        await asyncio.wait_for(fired.wait(), timeout=2.0)
    finally:
        await sched.stop()
