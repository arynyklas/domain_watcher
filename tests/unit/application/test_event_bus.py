"""InProcessEventBus: ordering, isolation, two-tier criticality, overflow."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from domain_watcher.application.event_bus import BusOverflow, InProcessEventBus
from domain_watcher.core.shared.events import Criticality, DomainEvent

if TYPE_CHECKING:
    import pytest

NOW = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class StdEvent(DomainEvent):
    payload: int = 0


@dataclass(frozen=True, slots=True)
class CritEvent(DomainEvent):
    payload: int = 0
    criticality: ClassVar[Criticality] = "critical"


async def test_callbacks_typed_match() -> None:
    bus = InProcessEventBus()
    seen: list[int] = []

    async def handler(e: StdEvent) -> None:
        seen.append(e.payload)

    bus.on(StdEvent, handler)
    await bus.publish(StdEvent(occurred_at=NOW, payload=7))
    await bus.publish(CritEvent(occurred_at=NOW, payload=99))
    assert seen == [7]


async def test_callback_exception_isolation(caplog: pytest.LogCaptureFixture) -> None:
    bus = InProcessEventBus()
    seen: list[int] = []

    async def bad(e: StdEvent) -> None:
        raise RuntimeError("boom")

    async def good(e: StdEvent) -> None:
        seen.append(e.payload)

    bus.on(StdEvent, bad)
    bus.on(StdEvent, good)
    with caplog.at_level(logging.ERROR):
        await bus.publish(StdEvent(occurred_at=NOW, payload=42))
    assert seen == [42]
    assert any("handler raised" in r.message for r in caplog.records)


async def test_iterator_yields_events() -> None:
    bus = InProcessEventBus()
    it = bus.events()

    async def producer() -> None:
        await bus.publish(StdEvent(occurred_at=NOW, payload=1))
        await bus.publish(StdEvent(occurred_at=NOW, payload=2))

    task = asyncio.create_task(producer())
    seen: list[int] = []
    for _ in range(2):
        evt = await asyncio.wait_for(it.__anext__(), timeout=1.0)
        assert isinstance(evt, StdEvent)
        seen.append(evt.payload)
    assert seen == [1, 2]
    await task


async def test_iterator_critical_preferred() -> None:
    bus = InProcessEventBus()
    it = bus.events()
    await bus.publish(StdEvent(occurred_at=NOW, payload=1))
    await bus.publish(CritEvent(occurred_at=NOW, payload=99))
    # Critical preferred even though standard was published first.
    first = await asyncio.wait_for(it.__anext__(), timeout=1.0)
    second = await asyncio.wait_for(it.__anext__(), timeout=1.0)
    assert isinstance(first, CritEvent)
    assert isinstance(second, StdEvent)


async def test_standard_overflow_emits_bus_overflow_event() -> None:
    bus = InProcessEventBus(standard_maxsize=2)
    it = bus.events()
    # Fill the queue without consuming.
    for i in range(5):
        await bus.publish(StdEvent(occurred_at=NOW, payload=i))
    # Drain — we expect to see the latest events plus a BusOverflow synthetic.
    drained: list[DomainEvent] = []
    for _ in range(5):
        try:
            evt = await asyncio.wait_for(it.__anext__(), timeout=0.1)
        except TimeoutError:
            break
        drained.append(evt)
    assert any(isinstance(e, BusOverflow) for e in drained), drained


async def test_critical_event_subscriber_timeout_logs_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = InProcessEventBus(critical_put_timeout=0.05, standard_maxsize=2)

    # First subscriber: a wedged iterator that never drains.
    wedged = bus.events()
    # Pre-fill the wedged iterator's critical queue is impossible (unbounded),
    # so we wedge by patching the internal subscriber to use a 0-sized queue.
    # Easiest: shrink the critical queue via the bus's first sub.
    sub = bus._subs[0]
    sub.critical_q = asyncio.Queue(maxsize=1)
    await sub.critical_q.put(CritEvent(occurred_at=NOW, payload=-1))  # pre-fill

    # Healthy subscriber:
    healthy_seen: list[int] = []

    async def healthy(e: CritEvent) -> None:
        healthy_seen.append(e.payload)

    bus.on(CritEvent, healthy)

    with caplog.at_level(logging.ERROR):
        await bus.publish(CritEvent(occurred_at=NOW, payload=42))

    assert any("critical event dropped" in r.message for r in caplog.records)
    assert healthy_seen == [42]
    # Cleanup
    _ = wedged


async def test_class_var_criticality_detection() -> None:
    assert StdEvent.criticality == "standard"
    assert CritEvent.criticality == "critical"
