"""``SchedulerService`` port and reconciliation contract.

The APScheduler adapter (Phase 8) implements this. The port lives in the
application layer so use cases can wire it without dragging cron parsing
into ``core/``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.shared.value_objects import DomainName

JobCallable = Callable[[], Awaitable[None]]


@runtime_checkable
class SchedulerService(Protocol):
    """Async scheduler with idempotent reconciliation.

    Implementations MUST give the job for ``domain`` the stable id
    ``f"check:{domain.value}"`` so reconciliation can match by id.
    """

    async def add_job(
        self,
        domain: DomainName,
        cron: str,
        callable_: JobCallable,
    ) -> None: ...

    async def add_or_update_job(
        self,
        domain: DomainName,
        cron: str,
        callable_: JobCallable,
    ) -> None: ...

    async def remove_job(self, domain: DomainName) -> None: ...

    def list_jobs(self) -> Sequence[str]: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def reconcile(
        self,
        domains: Iterable[MonitoredDomain],
        *,
        callable_factory: Callable[[MonitoredDomain], JobCallable],
    ) -> None: ...


class MemoryScheduler:
    """In-memory ``SchedulerService`` fake for unit tests.

    Records the (cron, callable) pair per domain. Tests inspect ``jobs`` and
    invoke ``trigger(domain)`` to simulate cron firing.
    """

    __slots__ = ("_started", "jobs")

    def __init__(self) -> None:
        self.jobs: dict[str, tuple[str, JobCallable]] = {}
        self._started = False

    async def add_job(
        self,
        domain: DomainName,
        cron: str,
        callable_: JobCallable,
    ) -> None:
        jid = self._jid(domain)
        if jid in self.jobs:
            raise ValueError(f"duplicate scheduler job {jid}")
        self.jobs[jid] = (cron, callable_)

    async def add_or_update_job(
        self,
        domain: DomainName,
        cron: str,
        callable_: JobCallable,
    ) -> None:
        self.jobs[self._jid(domain)] = (cron, callable_)

    async def remove_job(self, domain: DomainName) -> None:
        self.jobs.pop(self._jid(domain), None)

    def list_jobs(self) -> Sequence[str]:
        return tuple(self.jobs)

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def reconcile(
        self,
        domains: Iterable[MonitoredDomain],
        *,
        callable_factory: Callable[[MonitoredDomain], JobCallable],
    ) -> None:
        target: dict[str, tuple[str, JobCallable]] = {}
        for d in domains:
            target[self._jid(d.name)] = (d.schedule.cron, callable_factory(d))
        # Add or update
        for jid, (cron, fn) in target.items():
            existing = self.jobs.get(jid)
            if existing is None or existing[0] != cron:
                self.jobs[jid] = (cron, fn)
        # Remove jobs no longer present
        for jid in list(self.jobs):
            if jid not in target:
                del self.jobs[jid]

    async def trigger(self, domain: DomainName) -> None:
        """Test helper: invoke the job callable for ``domain`` if present."""
        existing = self.jobs.get(self._jid(domain))
        if existing is None:
            raise KeyError(f"no job for {domain.value}")
        await existing[1]()

    @staticmethod
    def _jid(domain: DomainName) -> str:
        return f"check:{domain.value}"


__all__ = ["JobCallable", "MemoryScheduler", "SchedulerService"]
