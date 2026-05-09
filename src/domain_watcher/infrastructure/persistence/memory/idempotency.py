"""In-memory ``IdempotencyStore`` keyed by (domain, threshold, cycle_id, channel)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from domain_watcher.core.monitoring.value_objects import ChannelId
    from domain_watcher.core.shared.value_objects import DomainName, Duration


class MemoryIdempotencyStore:
    """4-tuple keyed already-fired tracker."""

    __slots__ = ("_fired", "_lock")

    def __init__(self) -> None:
        self._fired: dict[tuple[str, int, str, str], datetime] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
    ) -> tuple[str, int, str, str]:
        return (domain.value, threshold.seconds, cycle_id, channel.value)

    async def already_fired(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
    ) -> bool:
        async with self._lock:
            return self._key(domain, threshold, cycle_id, channel) in self._fired

    async def record(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
        at: datetime,
    ) -> None:
        async with self._lock:
            self._fired[self._key(domain, threshold, cycle_id, channel)] = at


__all__ = ["MemoryIdempotencyStore"]
