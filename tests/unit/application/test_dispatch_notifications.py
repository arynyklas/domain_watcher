"""DispatchNotificationsUseCase: fan-out, retries, idempotency, exception isolation."""

from __future__ import annotations

from datetime import UTC, datetime

from domain_watcher.application.use_cases.dispatch_notifications import (
    DispatchNotificationsUseCase,
)
from domain_watcher.core.checking.policies import RetryPolicy
from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import ChannelId, CheckSchedule
from domain_watcher.core.notification.entities import Channel
from domain_watcher.core.notification.events import (
    NotificationDispatched,
    NotificationFailed,
)
from domain_watcher.core.notification.policies import NotificationPolicy
from domain_watcher.core.shared.errors import (
    DeliveryFailedError,
    NotificationError,
)
from domain_watcher.core.shared.time_provider import FixedClock
from domain_watcher.core.shared.value_objects import DomainName, Duration

from ._fakes import (
    FakeChannelResolver,
    FakeIdempotency,
    FakeNotifier,
    FakePublisher,
)

NOW = datetime(2026, 5, 9, tzinfo=UTC)
# 25 days away — crosses 30d threshold.
EXPIRES = NOW.replace(month=6, day=3)


def _domain() -> MonitoredDomain:
    return MonitoredDomain(
        name=DomainName("example.com"),
        schedule=CheckSchedule("0 */6 * * *"),
        checker_id="rdap",
        notify_thresholds=(Duration.days(30), Duration.days(7), Duration.days(1)),
        channels=(ChannelId("tg-ops"), ChannelId("email-team"), ChannelId("discord-eng")),
    )


def _ok_result(domain: DomainName) -> CheckResult:
    return CheckResult(
        domain=domain,
        outcome=CheckOutcome.OK,
        expires_at=EXPIRES,
        source="rdap",
    )


def _build(
    *,
    notifiers: dict,
    publisher: FakePublisher,
    idempotency: FakeIdempotency,
    resolver: FakeChannelResolver,
    retry: RetryPolicy = RetryPolicy(max_attempts=2),  # noqa: B008 — frozen, immutable
) -> DispatchNotificationsUseCase:
    sleeps: list[float] = []

    async def sleeper(s: float) -> None:
        sleeps.append(s)

    use_case = DispatchNotificationsUseCase(
        policy=NotificationPolicy((Duration.days(30), Duration.days(7), Duration.days(1))),
        resolver=resolver,
        idempotency=idempotency,
        notifiers=notifiers,
        publisher=publisher,
        clock=FixedClock(NOW),
        retry_policy=retry,
        sleeper=sleeper,
    )
    return use_case


async def test_dispatch_to_all_channels_records_idempotency() -> None:
    domain = _domain()
    notifier_tg = FakeNotifier()
    notifier_email = FakeNotifier()
    notifier_discord = FakeNotifier()
    publisher = FakePublisher()
    idempotency = FakeIdempotency()
    resolver = FakeChannelResolver(
        {
            domain.name.value: (
                Channel(id=ChannelId("tg-ops"), notifier_id="tg"),
                Channel(id=ChannelId("email-team"), notifier_id="email"),
                Channel(id=ChannelId("discord-eng"), notifier_id="discord"),
            )
        }
    )
    use_case = _build(
        notifiers={"tg": notifier_tg, "email": notifier_email, "discord": notifier_discord},
        publisher=publisher,
        idempotency=idempotency,
        resolver=resolver,
    )

    await use_case.execute(domain, previous=None, current=_ok_result(domain.name))

    assert len(notifier_tg.calls) == 1
    assert len(notifier_email.calls) == 1
    assert len(notifier_discord.calls) == 1
    assert sum(isinstance(e, NotificationDispatched) for e in publisher.events) == 3
    assert len(idempotency.fired) == 3


async def test_idempotent_skip_on_replay() -> None:
    domain = _domain()
    notifier_tg = FakeNotifier()
    publisher = FakePublisher()
    idempotency = FakeIdempotency()
    resolver = FakeChannelResolver(
        {domain.name.value: (Channel(id=ChannelId("tg-ops"), notifier_id="tg"),)}
    )
    use_case = _build(
        notifiers={"tg": notifier_tg},
        publisher=publisher,
        idempotency=idempotency,
        resolver=resolver,
    )
    await use_case.execute(domain, previous=None, current=_ok_result(domain.name))
    initial = len(notifier_tg.calls)
    await use_case.execute(domain, previous=None, current=_ok_result(domain.name))
    assert len(notifier_tg.calls) == initial  # nothing new sent


async def test_one_channel_permanent_others_succeed() -> None:
    domain = _domain()
    bad = FakeNotifier(behavior=[NotificationError("token revoked")])
    good_a = FakeNotifier()
    good_b = FakeNotifier()
    publisher = FakePublisher()
    idempotency = FakeIdempotency()
    resolver = FakeChannelResolver(
        {
            domain.name.value: (
                Channel(id=ChannelId("tg-ops"), notifier_id="tg"),
                Channel(id=ChannelId("email-team"), notifier_id="email"),
                Channel(id=ChannelId("discord-eng"), notifier_id="discord"),
            )
        }
    )
    use_case = _build(
        notifiers={"tg": bad, "email": good_a, "discord": good_b},
        publisher=publisher,
        idempotency=idempotency,
        resolver=resolver,
    )
    await use_case.execute(domain, previous=None, current=_ok_result(domain.name))

    successes = [e for e in publisher.events if isinstance(e, NotificationDispatched)]
    failures = [e for e in publisher.events if isinstance(e, NotificationFailed)]
    assert len(successes) == 2
    assert len(failures) == 1


async def test_delivery_failed_retries_then_succeeds() -> None:
    domain = _domain()
    flaky = FakeNotifier(behavior=[DeliveryFailedError("blip"), None])
    publisher = FakePublisher()
    idempotency = FakeIdempotency()
    resolver = FakeChannelResolver(
        {domain.name.value: (Channel(id=ChannelId("tg-ops"), notifier_id="tg"),)}
    )
    use_case = _build(
        notifiers={"tg": flaky},
        publisher=publisher,
        idempotency=idempotency,
        resolver=resolver,
    )
    await use_case.execute(domain, previous=None, current=_ok_result(domain.name))
    assert len(flaky.calls) == 2
    assert sum(isinstance(e, NotificationDispatched) for e in publisher.events) == 1


async def test_no_alerts_no_delivery() -> None:
    domain = _domain()
    notifier = FakeNotifier()
    publisher = FakePublisher()
    idempotency = FakeIdempotency()
    resolver = FakeChannelResolver(
        {domain.name.value: (Channel(id=ChannelId("tg-ops"), notifier_id="tg"),)}
    )
    use_case = _build(
        notifiers={"tg": notifier},
        publisher=publisher,
        idempotency=idempotency,
        resolver=resolver,
    )
    # current outcome non-OK → no alerts
    bad_result = CheckResult(
        domain=domain.name,
        outcome=CheckOutcome.PERMANENT_ERROR,
        expires_at=None,
        source="rdap",
        error="nx",
    )
    await use_case.execute(domain, previous=None, current=bad_result)
    assert notifier.calls == []
    assert publisher.events == []


async def test_unknown_notifier_publishes_failed() -> None:
    domain = _domain()
    publisher = FakePublisher()
    idempotency = FakeIdempotency()
    resolver = FakeChannelResolver(
        {domain.name.value: (Channel(id=ChannelId("tg-ops"), notifier_id="missing"),)}
    )
    use_case = _build(
        notifiers={},  # missing notifier id
        publisher=publisher,
        idempotency=idempotency,
        resolver=resolver,
    )
    await use_case.execute(domain, previous=None, current=_ok_result(domain.name))
    failures = [e for e in publisher.events if isinstance(e, NotificationFailed)]
    assert len(failures) == 1
