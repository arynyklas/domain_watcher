from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.notification.ports import IdempotencyStore
from domain_watcher.core.shared.value_objects import DomainName, Duration

from ._fakes import MemoryIdempotencyStore


@pytest.mark.asyncio
async def test_record_and_already_fired_for_same_key() -> None:
    s = MemoryIdempotencyStore()
    domain = DomainName("example.com")
    th = Duration.days(30)
    cid = "a" * 16
    ch = ChannelId("tg-ops")
    assert await s.already_fired(domain, th, cid, ch) is False
    await s.record(domain, th, cid, ch, datetime(2026, 5, 9, tzinfo=UTC))
    assert await s.already_fired(domain, th, cid, ch) is True


@pytest.mark.asyncio
async def test_different_cycle_id_not_suppressed() -> None:
    s = MemoryIdempotencyStore()
    domain = DomainName("example.com")
    th = Duration.days(30)
    ch = ChannelId("tg-ops")
    await s.record(domain, th, "a" * 16, ch, datetime(2026, 5, 9, tzinfo=UTC))
    assert await s.already_fired(domain, th, "b" * 16, ch) is False


@pytest.mark.asyncio
async def test_different_channel_not_suppressed() -> None:
    s = MemoryIdempotencyStore()
    domain = DomainName("example.com")
    th = Duration.days(30)
    cid = "a" * 16
    await s.record(
        domain, th, cid, ChannelId("tg-ops"), datetime(2026, 5, 9, tzinfo=UTC)
    )
    assert await s.already_fired(domain, th, cid, ChannelId("tg-eng")) is False


def test_memory_idempotency_satisfies_protocol() -> None:
    assert isinstance(MemoryIdempotencyStore(), IdempotencyStore)
