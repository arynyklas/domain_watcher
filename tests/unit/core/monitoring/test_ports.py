from __future__ import annotations

from typing import TYPE_CHECKING

from domain_watcher.core.monitoring.ports import MonitoredDomainRepository

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.shared.value_objects import DomainName


class _MapBackedFakeRepo:
    """Minimal in-test repository fake for satisfying the protocol contract."""

    def __init__(self) -> None:
        self._items: dict[DomainName, MonitoredDomain] = {}

    async def get(self, name: DomainName) -> MonitoredDomain | None:
        return self._items.get(name)

    async def add(self, domain: MonitoredDomain) -> None:
        self._items[domain.name] = domain

    async def update(self, domain: MonitoredDomain) -> None:
        self._items[domain.name] = domain

    async def remove(self, name: DomainName) -> None:
        self._items.pop(name, None)

    async def list_all(self) -> Sequence[MonitoredDomain]:
        return list(self._items.values())

    async def due_for_check(self, now: datetime) -> Sequence[MonitoredDomain]:
        return [d for d in self._items.values() if d.is_due(now)]


def test_fake_satisfies_repository_protocol() -> None:
    fake = _MapBackedFakeRepo()
    assert isinstance(fake, MonitoredDomainRepository)
