"""In-memory ``MonitoredDomainRepository``.

Used by tests and by the embedded library mode when the operator does not
wire a SQL backend. ``asyncio.Lock`` keeps mutations serial so concurrent
callers do not race on the dict update.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.shared.value_objects import DomainName


class MemoryMonitoredDomainRepo:
    """Dict-backed monitored-domain repo with an asyncio lock."""

    __slots__ = ("_lock", "_store")

    def __init__(self) -> None:
        self._store: dict[str, MonitoredDomain] = {}
        self._lock = asyncio.Lock()

    async def get(self, name: DomainName) -> MonitoredDomain | None:
        async with self._lock:
            return self._store.get(name.value)

    async def add(self, domain: MonitoredDomain) -> None:
        async with self._lock:
            if domain.name.value in self._store:
                raise ValueError(f"domain already exists: {domain.name.value}")
            self._store[domain.name.value] = domain

    async def update(self, domain: MonitoredDomain) -> None:
        async with self._lock:
            self._store[domain.name.value] = domain

    async def remove(self, name: DomainName) -> None:
        async with self._lock:
            self._store.pop(name.value, None)

    async def list_all(self) -> Sequence[MonitoredDomain]:
        async with self._lock:
            return tuple(self._store.values())

    async def due_for_check(self, now: datetime) -> Sequence[MonitoredDomain]:
        async with self._lock:
            return tuple(d for d in self._store.values() if d.is_due(now))


__all__ = ["MemoryMonitoredDomainRepo"]
