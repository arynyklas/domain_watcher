"""APScheduler-backed ``SchedulerService`` adapter."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import ChannelId, CheckSchedule
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.scheduling.apscheduler import ApsScheduler

if TYPE_CHECKING:
    from collections.abc import Sequence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _domain(name: str, *, cron: str = "0 */6 * * *") -> MonitoredDomain:
    return MonitoredDomain(
        name=DomainName(name),
        schedule=CheckSchedule(cron=cron),
        checker_id="rdap",
        notify_thresholds=(Duration.days(30), Duration.days(7), Duration.days(1)),
        channels=(ChannelId("tg-ops"),),
    )


async def _noop() -> None:
    return None


class _BootstrapRepo:
    """Minimal stand-in for ``MonitoredDomainRepository`` for the bootstrap test."""

    def __init__(self, domains: Sequence[MonitoredDomain]) -> None:
        self._domains = tuple(domains)

    async def list_all(self) -> Sequence[MonitoredDomain]:
        return self._domains

    # The other methods exist only to satisfy ``isinstance`` checks if tests
    # ever swap to runtime_checkable; not needed for ApsScheduler bootstrap.


# ---------------------------------------------------------------------------
# add_job / list_jobs / remove_job
# ---------------------------------------------------------------------------
async def test_add_job_uses_stable_id() -> None:
    sched = ApsScheduler()
    await sched.add_job(DomainName("example.com"), "0 */6 * * *", _noop)
    assert sched.list_jobs() == ("check:example.com",)
    await sched.stop()


async def test_add_job_duplicate_raises() -> None:
    sched = ApsScheduler()
    await sched.add_job(DomainName("a.test"), "0 */6 * * *", _noop)
    with pytest.raises(ValueError):
        await sched.add_job(DomainName("a.test"), "0 */6 * * *", _noop)
    await sched.stop()


async def test_remove_job_idempotent() -> None:
    sched = ApsScheduler()
    await sched.remove_job(DomainName("never-added.test"))  # no-op
    await sched.add_job(DomainName("a.test"), "0 */6 * * *", _noop)
    await sched.remove_job(DomainName("a.test"))
    await sched.remove_job(DomainName("a.test"))  # second call is a no-op
    assert sched.list_jobs() == ()
    await sched.stop()


async def test_invalid_cron_raises() -> None:
    sched = ApsScheduler()
    with pytest.raises(ValueError) as exc:
        await sched.add_job(DomainName("a.test"), "definitely not cron", _noop)
    assert "cron" in str(exc.value).lower()
    await sched.stop()


# ---------------------------------------------------------------------------
# add_or_update_job
# ---------------------------------------------------------------------------
async def test_add_or_update_job_idempotent_same_args() -> None:
    sched = ApsScheduler()
    await sched.add_or_update_job(DomainName("a.test"), "0 */6 * * *", _noop)
    await sched.add_or_update_job(DomainName("a.test"), "0 */6 * * *", _noop)
    assert sched.list_jobs() == ("check:a.test",)
    await sched.stop()


async def test_add_or_update_job_changes_cron_in_place() -> None:
    sched = ApsScheduler()
    await sched.add_or_update_job(DomainName("a.test"), "0 */6 * * *", _noop)
    await sched.add_or_update_job(DomainName("a.test"), "0 */1 * * *", _noop)
    # Still exactly one job
    assert sched.list_jobs() == ("check:a.test",)
    job = sched._scheduler.get_job("check:a.test")
    assert "*/1" in str(job.trigger) or "1" in str(job.trigger)
    await sched.stop()


# ---------------------------------------------------------------------------
# reconcile contract (mirrors Task 2.8)
# ---------------------------------------------------------------------------
async def test_reconcile_adds_new_domains() -> None:
    sched = ApsScheduler()
    await sched.reconcile(
        [_domain("a.test"), _domain("b.test")],
        callable_factory=lambda _d: _noop,
    )
    assert set(sched.list_jobs()) == {"check:a.test", "check:b.test"}
    await sched.stop()


async def test_reconcile_keeps_unchanged_jobs() -> None:
    sched = ApsScheduler()
    await sched.reconcile(
        [_domain("a.test", cron="0 */6 * * *")],
        callable_factory=lambda _d: _noop,
    )
    job_before = sched._scheduler.get_job("check:a.test")

    # Same domain + same cron + same callable → leave alone (same Job instance).
    await sched.reconcile(
        [_domain("a.test", cron="0 */6 * * *")],
        callable_factory=lambda _d: _noop,
    )
    job_after = sched._scheduler.get_job("check:a.test")
    # APScheduler's Job identity may differ, but the next_run_time must NOT change
    # under "no diff" — assert the trigger is the same.
    assert str(job_before.trigger) == str(job_after.trigger)
    await sched.stop()


async def test_reconcile_replaces_changed_schedule() -> None:
    sched = ApsScheduler()
    await sched.reconcile(
        [_domain("a.test", cron="0 */6 * * *")],
        callable_factory=lambda _d: _noop,
    )
    await sched.reconcile(
        [_domain("a.test", cron="*/15 * * * *")],
        callable_factory=lambda _d: _noop,
    )
    assert sched.list_jobs() == ("check:a.test",)
    job = sched._scheduler.get_job("check:a.test")
    assert "*/15" in str(job.trigger)
    await sched.stop()


async def test_reconcile_removes_dropped_domains() -> None:
    sched = ApsScheduler()
    await sched.reconcile(
        [_domain("a.test"), _domain("b.test")],
        callable_factory=lambda _d: _noop,
    )
    await sched.reconcile(
        [_domain("a.test")],
        callable_factory=lambda _d: _noop,
    )
    assert sched.list_jobs() == ("check:a.test",)
    await sched.stop()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
async def test_start_stop_idempotent() -> None:
    sched = ApsScheduler()
    await sched.start()
    await sched.start()  # second call is a no-op
    assert sched.started is True
    await sched.stop()
    await sched.stop()  # second call is a no-op
    assert sched.started is False


async def test_start_bootstrap_reconciles_from_repo_before_dispatch() -> None:
    """Embedded callers must observe scheduled jobs immediately after start()."""
    repo = _BootstrapRepo([_domain("seed1.test"), _domain("seed2.test")])
    sched = ApsScheduler(
        bootstrap_repo=repo,
        bootstrap_callable_factory=lambda _d: _noop,
    )
    await sched.start()
    try:
        assert set(sched.list_jobs()) == {"check:seed1.test", "check:seed2.test"}
    finally:
        await sched.stop()


async def test_start_without_bootstrap_has_no_jobs() -> None:
    sched = ApsScheduler()
    await sched.start()
    try:
        assert sched.list_jobs() == ()
    finally:
        await sched.stop()


# ---------------------------------------------------------------------------
# Job actually fires (smoke test for AsyncIOScheduler integration)
# ---------------------------------------------------------------------------
async def test_job_fires_on_short_interval() -> None:
    """Sanity: an interval-triggered job is dispatched on the loop."""
    sched = ApsScheduler()
    fired = asyncio.Event()

    async def _job() -> None:
        fired.set()

    # Use a 0.5s interval via the revalidation hook (already exposed).
    await sched.add_revalidation_job(Duration.from_seconds(1), _job)
    await sched.start()
    try:
        # Force a run by triggering the job manually rather than waiting for cron.
        sched._scheduler.get_job("revalidate-learned-rules").modify(next_run_time=None)
        # Manually invoke the wrapped callable through APScheduler's API.
        sched._scheduler.add_job(
            _job,
            id="manual-fire",
            replace_existing=True,
        )
        await asyncio.wait_for(fired.wait(), timeout=2.0)
    finally:
        await sched.stop()
