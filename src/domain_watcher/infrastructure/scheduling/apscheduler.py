"""APScheduler-backed implementation of ``SchedulerService``.

Uses ``AsyncIOScheduler`` so jobs run on the current event loop. Each
domain becomes one cron job with stable id ``f"check:{domain.name.value}"``;
``reconcile`` does a 3-way diff (add, update-in-place, remove) so the
job set tracks the config without recreating untouched jobs.

A revalidation job (Task 8.2) is scheduled on a fixed interval and is
distinguished by a separate stable id (``"revalidate-learned-rules"``)
so the per-domain reconcile loop never touches it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.shared.value_objects import DomainName, Duration


@runtime_checkable
class _BootstrapDomainSource(Protocol):
    """Narrow read-only port for ``ApsScheduler.start`` bootstrap.

    Full ``MonitoredDomainRepository`` instances satisfy this; tests can
    pass a minimal stub without implementing add/update/remove.
    """

    async def list_all(self) -> Sequence[MonitoredDomain]: ...


_log = logging.getLogger(__name__)

JobCallable = Callable[[], "Awaitable[None]"]
"""Async no-arg callable; APScheduler runs it on the asyncio loop."""

_REVALIDATION_JOB_ID = "revalidate-learned-rules"


def _job_id(name: DomainName) -> str:
    return f"check:{name.value}"


class ApsScheduler:
    """``SchedulerService`` adapter built on ``apscheduler`` 3.x.

    Construction never starts the scheduler. ``start()`` is idempotent:
    if a ``bootstrap_repo`` and ``bootstrap_callable_factory`` were
    provided, it reconciles from ``repo.list_all()`` BEFORE returning.
    Embedded callers therefore see the persisted domain set scheduled
    before the first ``ensure_watching`` or hot-reload event.
    """

    def __init__(
        self,
        *,
        timezone: str = "UTC",
        bootstrap_repo: _BootstrapDomainSource | None = None,
        bootstrap_callable_factory: Callable[[MonitoredDomain], JobCallable] | None = None,
    ) -> None:
        self._scheduler = AsyncIOScheduler(
            timezone=timezone,
            job_defaults={
                "coalesce": True,  # collapse missed runs into one
                "misfire_grace_time": None,  # drop misfires; we'll catch up next slot
                "max_instances": 1,
            },
        )
        self._timezone = timezone
        self._bootstrap_repo = bootstrap_repo
        self._bootstrap_callable_factory = bootstrap_callable_factory
        self._started = False
        # Map job_id → (cron, callable) so reconcile can detect changes
        # without poking APScheduler internals.
        self._jobs: dict[str, tuple[str, JobCallable]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        # Bootstrap reconcile must happen BEFORE the scheduler dispatches
        # any tick — otherwise an embedded caller could observe an empty
        # job list. We add the jobs first (the scheduler will pick them up
        # on .start()), then start it.
        if self._bootstrap_repo is not None and self._bootstrap_callable_factory is not None:
            domains = await self._bootstrap_repo.list_all()
            await self.reconcile(
                domains,
                callable_factory=self._bootstrap_callable_factory,
            )
        self._scheduler.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        # ``shutdown(wait=True)`` blocks until in-flight jobs finish; offload
        # so we don't stall the loop.
        await asyncio.to_thread(self._scheduler.shutdown, True)
        self._started = False
        self._jobs.clear()

    @property
    def started(self) -> bool:
        return self._started

    # ------------------------------------------------------------------
    # add / add_or_update / remove
    # ------------------------------------------------------------------
    async def add_job(
        self,
        domain: DomainName,
        cron: str,
        callable_: JobCallable,
    ) -> None:
        jid = _job_id(domain)
        if jid in self._jobs:
            raise ValueError(f"duplicate scheduler job {jid}")
        self._add_aps_job(jid, cron, callable_)
        self._jobs[jid] = (cron, callable_)

    async def add_or_update_job(
        self,
        domain: DomainName,
        cron: str,
        callable_: JobCallable,
    ) -> None:
        jid = _job_id(domain)
        existing = self._jobs.get(jid)
        if existing is not None and existing[0] == cron and existing[1] is callable_:
            return  # already at desired state — true no-op
        # Replace (or add) atomically: APScheduler's add_job(..., replace_existing=True)
        # handles both cases.
        self._add_aps_job(jid, cron, callable_, replace=True)
        self._jobs[jid] = (cron, callable_)

    async def remove_job(self, domain: DomainName) -> None:
        jid = _job_id(domain)
        if jid not in self._jobs:
            return
        if self._scheduler.get_job(jid) is not None:
            self._scheduler.remove_job(jid)
        del self._jobs[jid]

    def list_jobs(self) -> Sequence[str]:
        return tuple(self._jobs)

    # ------------------------------------------------------------------
    # reconcile
    # ------------------------------------------------------------------
    async def reconcile(
        self,
        domains: Iterable[MonitoredDomain],
        *,
        callable_factory: Callable[[MonitoredDomain], JobCallable],
    ) -> None:
        target: dict[str, tuple[str, JobCallable]] = {}
        for d in domains:
            jid = _job_id(d.name)
            target[jid] = (d.schedule.cron, callable_factory(d))

        # Add or update.
        for jid, (cron, fn) in target.items():
            existing = self._jobs.get(jid)
            if existing is None:
                self._add_aps_job(jid, cron, fn)
                self._jobs[jid] = (cron, fn)
                continue
            if existing[0] != cron:
                # Schedule changed — replace in-place.
                self._add_aps_job(jid, cron, fn, replace=True)
                self._jobs[jid] = (cron, fn)
            # If only callable changed (same cron), keep the existing job —
            # APScheduler invokes whatever ``func`` we last installed; rebind.
            elif existing[1] is not fn:
                self._add_aps_job(jid, cron, fn, replace=True)
                self._jobs[jid] = (cron, fn)

        # Remove jobs that disappeared.
        for jid in list(self._jobs):
            if jid in target:
                continue
            if jid == _REVALIDATION_JOB_ID:
                continue  # never touched by domain reconcile
            if self._scheduler.get_job(jid) is not None:
                self._scheduler.remove_job(jid)
            del self._jobs[jid]

    # ------------------------------------------------------------------
    # Revalidation job (Task 8.2)
    # ------------------------------------------------------------------
    async def add_revalidation_job(
        self,
        interval: Duration,
        callable_: JobCallable,
    ) -> None:
        """Schedule the periodic learned-rules revalidation pass."""
        if interval.seconds <= 0:
            raise ValueError(f"revalidation interval must be positive, got {interval.seconds}s")
        jid = _REVALIDATION_JOB_ID
        trigger = IntervalTrigger(seconds=interval.seconds, timezone=self._timezone)
        if self._scheduler.get_job(jid) is not None:
            self._scheduler.remove_job(jid)
        self._scheduler.add_job(
            _wrap(callable_),
            trigger=trigger,
            id=jid,
        )
        self._jobs[jid] = (f"interval:{interval.seconds}s", callable_)

    async def remove_revalidation_job(self) -> None:
        jid = _REVALIDATION_JOB_ID
        if self._scheduler.get_job(jid) is not None:
            self._scheduler.remove_job(jid)
        self._jobs.pop(jid, None)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _add_aps_job(
        self,
        jid: str,
        cron: str,
        callable_: JobCallable,
        *,
        replace: bool = False,
    ) -> None:
        try:
            trigger = CronTrigger.from_crontab(cron, timezone=self._timezone)
        except ValueError as exc:
            raise ValueError(f"invalid cron {cron!r}: {exc}") from exc
        # APScheduler's ``replace_existing=True`` is a no-op while the
        # scheduler is unstarted (jobs live in a pending list that does
        # not honour replacement). Remove first so the new trigger sticks
        # both before and after ``start()``.
        if replace and self._scheduler.get_job(jid) is not None:
            self._scheduler.remove_job(jid)
        self._scheduler.add_job(
            _wrap(callable_),
            trigger=trigger,
            id=jid,
        )


def _wrap(callable_: JobCallable) -> Callable[[], Awaitable[None]]:
    """Indirection that prevents APScheduler from inspecting the closure."""

    async def _runner() -> None:
        try:
            await callable_()
        except Exception:
            _log.exception("scheduler job raised")

    return _runner


__all__ = ["ApsScheduler", "JobCallable"]
