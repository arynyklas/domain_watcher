"""SQL-backed ``IdempotencyStore`` (4-tuple primary key)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from domain_watcher.infrastructure.persistence.sql.orm import AlertIdempotencyRow

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from domain_watcher.core.monitoring.value_objects import ChannelId
    from domain_watcher.core.shared.value_objects import DomainName, Duration


class SqlIdempotencyStore:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def already_fired(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
    ) -> bool:
        stmt = select(AlertIdempotencyRow).where(
            AlertIdempotencyRow.domain_name == domain.value,
            AlertIdempotencyRow.threshold_secs == threshold.seconds,
            AlertIdempotencyRow.cycle_id == cycle_id,
            AlertIdempotencyRow.channel_id == channel.value,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def record(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
        at: datetime,
    ) -> None:
        row = AlertIdempotencyRow(
            domain_name=domain.value,
            threshold_secs=threshold.seconds,
            cycle_id=cycle_id,
            channel_id=channel.value,
            fired_at=at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            # Another concurrent writer already recorded — idempotent record
            # by definition: swallow.
            await self._session.rollback()


__all__ = ["SqlIdempotencyStore"]
