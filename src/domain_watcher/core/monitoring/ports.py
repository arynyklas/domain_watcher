"""Repository ports for the monitoring bounded context (ADR 0002 §2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.shared.value_objects import DomainName


@runtime_checkable
class MonitoredDomainRepository(Protocol):
    """CRUD + due-for-check query.

    Intentionally narrow: the bot repo's tenant and subscription concerns
    live elsewhere. ``due_for_check`` exists so the scheduler can ask the
    repo what to fan out without dragging cron parsing into core.
    """

    async def get(self, name: DomainName) -> MonitoredDomain | None: ...

    async def add(self, domain: MonitoredDomain) -> None: ...

    async def update(self, domain: MonitoredDomain) -> None: ...

    async def remove(self, name: DomainName) -> None: ...

    async def list_all(self) -> Sequence[MonitoredDomain]: ...

    async def due_for_check(self, now: datetime) -> Sequence[MonitoredDomain]: ...


__all__ = ["MonitoredDomainRepository"]
