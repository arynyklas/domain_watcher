"""SQL-backed ``MonitoredDomainRepository``.

The aggregate is denormalised — thresholds and channels live as
comma-separated text columns. This keeps the schema simple; queries are
keyed by ``name``, never by individual thresholds, so a child table buys
nothing.
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import (
    ChannelId,
    CheckSchedule,
    LastCheck,
)
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.persistence.sql.orm import MonitoredDomainRow

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _row_to_domain(row: MonitoredDomainRow) -> MonitoredDomain:
    thresholds = tuple(
        Duration.from_seconds(int(s))
        for s in row.notify_thresholds_secs.split(",")
        if s
    )
    channels = tuple(ChannelId(c) for c in row.channels.split(",") if c)
    last_check = None
    last_at = _ensure_utc(row.last_check_at)
    last_expires = _ensure_utc(row.last_check_expires_at)
    if last_at is not None:
        last_check = LastCheck(
            at=last_at,
            outcome=CheckOutcome(row.last_check_outcome or CheckOutcome.OK.value),
            expires_at=last_expires,
        )
    return MonitoredDomain(
        name=DomainName(row.name),
        schedule=CheckSchedule(row.cron),
        checker_id=row.checker_id,
        notify_thresholds=thresholds,
        channels=channels,
        last_check=last_check,
        metadata=json.loads(row.metadata_json or "{}"),
    )


def _domain_to_values(domain: MonitoredDomain) -> dict[str, object]:
    last = domain.last_check
    return {
        "name": domain.name.value,
        "cron": domain.schedule.cron,
        "checker_id": domain.checker_id,
        "notify_thresholds_secs": ",".join(
            str(d.seconds) for d in domain.notify_thresholds
        ),
        "channels": ",".join(c.value for c in domain.channels),
        "metadata_json": json.dumps(dict(domain.metadata), sort_keys=True),
        "last_check_at": last.at if last is not None else None,
        "last_check_outcome": last.outcome.value if last is not None else None,
        "last_check_expires_at": last.expires_at if last is not None else None,
    }


class SqlMonitoredDomainRepo:
    """SQLAlchemy adapter for ``MonitoredDomainRepository``."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, name: DomainName) -> MonitoredDomain | None:
        row = await self._session.get(MonitoredDomainRow, name.value)
        if row is None:
            return None
        return _row_to_domain(row)

    async def add(self, domain: MonitoredDomain) -> None:
        existing = await self._session.get(MonitoredDomainRow, domain.name.value)
        if existing is not None:
            raise ValueError(f"domain already exists: {domain.name.value}")
        row = MonitoredDomainRow(**_domain_to_values(domain))
        self._session.add(row)
        await self._session.flush()

    async def update(self, domain: MonitoredDomain) -> None:
        existing = await self._session.get(MonitoredDomainRow, domain.name.value)
        values = _domain_to_values(domain)
        if existing is None:
            row = MonitoredDomainRow(**values)
            self._session.add(row)
        else:
            for k, v in values.items():
                setattr(existing, k, v)
        await self._session.flush()

    async def remove(self, name: DomainName) -> None:
        existing = await self._session.get(MonitoredDomainRow, name.value)
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()

    async def list_all(self) -> Sequence[MonitoredDomain]:
        result = await self._session.execute(select(MonitoredDomainRow))
        return tuple(_row_to_domain(r) for r in result.scalars().all())

    async def due_for_check(self, now: datetime) -> Sequence[MonitoredDomain]:
        # Authoritative due-ness is the cron scheduler; this method exists
        # for embedded callers. Defer to the entity's ``is_due``.
        all_rows = await self.list_all()
        return tuple(d for d in all_rows if d.is_due(now))


_ = sqlite_insert  # reserved for future upsert paths; keep import discoverable
_: Iterable[object] = ()


__all__ = ["SqlMonitoredDomainRepo"]
