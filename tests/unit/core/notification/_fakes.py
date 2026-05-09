"""In-memory ``IdempotencyStore`` fake for notification tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from domain_watcher.core.monitoring.value_objects import ChannelId
    from domain_watcher.core.shared.value_objects import DomainName, Duration


class MemoryIdempotencyStore:
    """4-tuple-keyed in-memory idempotency store."""

    def __init__(self) -> None:
        self._records: set[tuple[str, int, str, str]] = set()

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
        return self._key(domain, threshold, cycle_id, channel) in self._records

    async def record(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
        at: datetime,
    ) -> None:
        del at  # unused; in-memory store doesn't persist time
        self._records.add(self._key(domain, threshold, cycle_id, channel))
