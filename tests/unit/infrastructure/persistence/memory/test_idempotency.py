"""MemoryIdempotencyStore: 4-tuple key, renewal yields new cycle, per-channel."""

from __future__ import annotations

from datetime import UTC, datetime

from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.persistence.memory import MemoryIdempotencyStore

NOW = datetime(2026, 5, 9, tzinfo=UTC)
DOMAIN = DomainName("example.com")
THRESHOLD = Duration.days(30)
CYCLE_A = "a" * 16
CYCLE_B = "b" * 16
CHANNEL_X = ChannelId("tg-ops")
CHANNEL_Y = ChannelId("email-team")


async def test_record_then_already_fired() -> None:
    store = MemoryIdempotencyStore()
    assert not await store.already_fired(DOMAIN, THRESHOLD, CYCLE_A, CHANNEL_X)
    await store.record(DOMAIN, THRESHOLD, CYCLE_A, CHANNEL_X, NOW)
    assert await store.already_fired(DOMAIN, THRESHOLD, CYCLE_A, CHANNEL_X)


async def test_different_cycle_id_distinct() -> None:
    store = MemoryIdempotencyStore()
    await store.record(DOMAIN, THRESHOLD, CYCLE_A, CHANNEL_X, NOW)
    assert not await store.already_fired(DOMAIN, THRESHOLD, CYCLE_B, CHANNEL_X)


async def test_different_channel_distinct() -> None:
    store = MemoryIdempotencyStore()
    await store.record(DOMAIN, THRESHOLD, CYCLE_A, CHANNEL_X, NOW)
    assert not await store.already_fired(DOMAIN, THRESHOLD, CYCLE_A, CHANNEL_Y)
