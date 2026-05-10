"""Tests for ``DomainWatcher`` façade + ``DomainWatcherBuilder`` (Task 9.1).

Uses the in-memory fakes everywhere; no network, no filesystem. The
public surface MUST stay green even when the bot repo (out of scope)
rebases on top of us.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from domain_watcher import (
    DomainCheckCompleted,
    DomainEvent,
    DomainName,
    DomainWatcher,
    DomainWatcherBuilder,
    Duration,
)
from domain_watcher.application.scheduling import MemoryScheduler
from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.shared.time_provider import FixedClock


class FakeChecker:
    """Returns a configurable fixed expires_at to drive the dispatch path."""

    id: ClassVar[str] = "fake"

    def __init__(self, expires_at: datetime) -> None:
        self._expires_at = expires_at
        self.calls = 0

    async def check(self, domain: DomainName) -> CheckResult:
        self.calls += 1
        return CheckResult(
            domain=domain,
            outcome=CheckOutcome.OK,
            expires_at=self._expires_at,
            source=self.id,
        )


class RecordingNotifier:
    """Captures ``send`` invocations so tests can assert on dispatch."""

    id: ClassVar[str] = "recording"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, alert, channel) -> None:  # type: ignore[no-untyped-def]
        self.sent.append((alert.domain.value, channel.id.value))


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def test_builder_requires_at_least_one_checker() -> None:
    with pytest.raises(ValueError, match="checker"):
        DomainWatcherBuilder().with_notifier(RecordingNotifier()).build()


def test_builder_requires_at_least_one_notifier() -> None:
    with pytest.raises(ValueError, match="notifier"):
        DomainWatcherBuilder().with_checker(
            FakeChecker(datetime(2030, 1, 1, tzinfo=UTC))
        ).build()


def test_builder_registers_passed_in_plugins() -> None:
    checker = FakeChecker(datetime(2030, 1, 1, tzinfo=UTC))
    notifier = RecordingNotifier()
    watcher = (
        DomainWatcherBuilder()
        .with_checker(checker)
        .with_notifier(notifier, channel_id="ops")
        .build()
    )
    assert checker in tuple(watcher.checker_registry)
    assert notifier in tuple(watcher.notifier_registry)


def test_builder_default_thresholds_setter() -> None:
    watcher = (
        DomainWatcherBuilder()
        .with_checker(FakeChecker(datetime(2030, 1, 1, tzinfo=UTC)))
        .with_notifier(RecordingNotifier())
        .with_default_thresholds(Duration.days(60), Duration.days(7))
        .build()
    )
    assert watcher.default_thresholds == (Duration.days(60), Duration.days(7))


# ---------------------------------------------------------------------------
# Façade lifecycle
# ---------------------------------------------------------------------------


def _make_watcher(
    *,
    expires_at: datetime,
    clock: FixedClock,
) -> tuple[DomainWatcher, FakeChecker, RecordingNotifier, MemoryScheduler]:
    checker = FakeChecker(expires_at)
    notifier = RecordingNotifier()
    scheduler = MemoryScheduler()
    watcher = (
        DomainWatcherBuilder()
        .with_checker(checker)
        .with_notifier(notifier, channel_id="ops")
        .with_default_thresholds(Duration.days(30), Duration.days(7), Duration.days(1))
        .with_clock(clock)
        .with_scheduler(scheduler)
        .build()
    )
    return watcher, checker, notifier, scheduler


async def test_start_is_idempotent_and_boots_scheduler() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    watcher, _c, _n, scheduler = _make_watcher(
        expires_at=datetime(2026, 12, 1, tzinfo=UTC), clock=clock
    )
    await watcher.start()
    await watcher.start()  # idempotent
    assert scheduler.started is True
    await watcher.stop()
    await watcher.stop()  # idempotent
    assert scheduler.started is False


async def test_ensure_watching_is_idempotent_and_upserts_repo() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    watcher, _c, _n, scheduler = _make_watcher(
        expires_at=datetime(2026, 12, 1, tzinfo=UTC), clock=clock
    )
    await watcher.start()
    name = DomainName("example.com")
    await watcher.ensure_watching(name, checker_id="fake", channels=[ChannelId("ops")])
    first_jobs = tuple(scheduler.list_jobs())
    await watcher.ensure_watching(name, checker_id="fake", channels=[ChannelId("ops")])
    second_jobs = tuple(scheduler.list_jobs())
    assert first_jobs == second_jobs
    assert (await watcher.repo.list_all())[0].name == name
    await watcher.stop()


async def test_remove_watching_cancels_job_and_keeps_idempotency_rows() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    expires = datetime(2026, 1, 25, tzinfo=UTC)  # 24 days away → crosses 30d threshold
    watcher, _c, notifier, scheduler = _make_watcher(expires_at=expires, clock=clock)
    await watcher.start()
    name = DomainName("example.com")
    await watcher.ensure_watching(name, checker_id="fake", channels=[ChannelId("ops")])
    await watcher.check_now(name)
    assert notifier.sent  # at least one alert dispatched

    # Snapshot idempotency keys via repeated already_fired probes.
    threshold = Duration.days(30)
    cycle_id = _cycle_id(expires)
    fired_before_remove = await watcher.idempotency.already_fired(
        name, threshold, cycle_id, ChannelId("ops")
    )
    assert fired_before_remove is True

    await watcher.remove_watching(name)
    assert tuple(scheduler.list_jobs()) == ()
    assert await watcher.repo.get(name) is None

    # Idempotency record SURVIVES removal (re-adding must not re-page).
    fired_after_remove = await watcher.idempotency.already_fired(
        name, threshold, cycle_id, ChannelId("ops")
    )
    assert fired_after_remove is True
    await watcher.stop()


async def test_check_now_publishes_check_completed_event() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    watcher, _c, _n, _s = _make_watcher(
        expires_at=datetime(2026, 6, 1, tzinfo=UTC), clock=clock
    )
    await watcher.start()
    received: list[DomainEvent] = []

    async def _handler(event: DomainCheckCompleted) -> None:
        received.append(event)

    watcher.on(DomainCheckCompleted, _handler)
    name = DomainName("example.com")
    await watcher.ensure_watching(name, checker_id="fake", channels=[ChannelId("ops")])
    await watcher.check_now(name)
    assert len(received) == 1
    assert isinstance(received[0], DomainCheckCompleted)
    await watcher.stop()


async def test_events_iterator_yields_published_events() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    watcher, _c, _n, _s = _make_watcher(
        expires_at=datetime(2026, 6, 1, tzinfo=UTC), clock=clock
    )
    await watcher.start()
    name = DomainName("example.com")
    await watcher.ensure_watching(name, checker_id="fake", channels=[ChannelId("ops")])

    iterator = watcher.events()
    pull_task = asyncio.create_task(_collect_events(iterator, n=1))
    await asyncio.sleep(0)  # let the iterator subscribe
    await watcher.check_now(name)
    received = await asyncio.wait_for(pull_task, timeout=2.0)
    assert any(isinstance(e, DomainCheckCompleted) for e in received)
    await watcher.stop()


async def test_check_now_unknown_domain_raises() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    watcher, _c, _n, _s = _make_watcher(
        expires_at=datetime(2026, 6, 1, tzinfo=UTC), clock=clock
    )
    await watcher.start()
    with pytest.raises(LookupError):
        await watcher.check_now(DomainName("nothing.example"))
    await watcher.stop()


async def test_start_reconciles_initial_domains_into_scheduler() -> None:
    """Embedded mode: ``initial_domains`` populated via builder lands in scheduler."""
    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.monitoring.value_objects import CheckSchedule

    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    checker = FakeChecker(datetime(2026, 12, 1, tzinfo=UTC))
    notifier = RecordingNotifier()
    scheduler = MemoryScheduler()
    name = DomainName("seed.example")
    seed = MonitoredDomain(
        name=name,
        schedule=CheckSchedule("0 0 * * *"),
        checker_id="fake",
        notify_thresholds=(Duration.days(30), Duration.days(7), Duration.days(1)),
        channels=(ChannelId("ops"),),
    )
    watcher = (
        DomainWatcherBuilder()
        .with_checker(checker)
        .with_notifier(notifier, channel_id="ops")
        .with_clock(clock)
        .with_scheduler(scheduler)
        .with_initial_domain(seed)
        .build()
    )
    # MemoryScheduler doesn't autoreconcile; the façade must seed the repo
    # on start so the user can drive checks immediately afterwards.
    await watcher.start()
    assert (await watcher.repo.get(name)) is not None
    await watcher.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cycle_id(expires_at: datetime) -> str:
    import hashlib

    return hashlib.sha256(expires_at.isoformat().encode()).hexdigest()[:16]


async def _collect_events(it, n: int) -> Sequence[DomainEvent]:
    out: list[DomainEvent] = []
    async for event in it:
        out.append(event)
        if len(out) >= n:
            break
    return tuple(out)
