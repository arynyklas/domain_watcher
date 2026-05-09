"""CheckDomainUseCase: success, transient retries, permanent, missing entries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

from domain_watcher.application.use_cases.check_domain import (
    CheckDomainUseCase,
    CheckerNotRegisteredError,
    DomainNotMonitoredError,
)
from domain_watcher.core.checking.events import (
    DomainCheckCompleted,
    DomainCheckFailed,
)
from domain_watcher.core.checking.policies import RetryPolicy
from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import ChannelId, CheckSchedule
from domain_watcher.core.shared.errors import (
    PermanentCheckError,
    TransientCheckError,
)
from domain_watcher.core.shared.time_provider import FixedClock
from domain_watcher.core.shared.value_objects import DomainName, Duration

from ._fakes import (
    FakeChecker,
    FakeMonitoredRepo,
    FakePublisher,
    make_ok_result,
    make_permanent_result,
    make_transient_result,
)

NOW = datetime(2026, 5, 9, tzinfo=UTC)
EXPIRES = datetime(2026, 6, 9, tzinfo=UTC)


def _domain(name: str = "example.com") -> MonitoredDomain:
    return MonitoredDomain(
        name=DomainName(name),
        schedule=CheckSchedule("0 */6 * * *"),
        checker_id="fake",
        notify_thresholds=(Duration.days(30), Duration.days(1)),
        channels=(ChannelId("tg-ops"),),
    )


Sleeper = Callable[[float], Awaitable[None]]


def _zero_sleep_factory() -> tuple[list[float], Sleeper]:
    sleeps: list[float] = []

    async def sleeper(s: float) -> None:
        sleeps.append(s)

    return sleeps, sleeper


def _build(
    *,
    repo: FakeMonitoredRepo,
    checker: FakeChecker,
    pub: FakePublisher,
    sleeper,
    retry: RetryPolicy = RetryPolicy(max_attempts=3),  # noqa: B008 — frozen, immutable
) -> CheckDomainUseCase:
    return CheckDomainUseCase(
        repo=repo,
        checkers={"fake": checker},
        retry_policy=retry,
        publisher=pub,
        clock=FixedClock(NOW),
        sleeper=sleeper,
    )


async def test_happy_path_persists_and_publishes() -> None:
    repo = FakeMonitoredRepo()
    domain = _domain()
    await repo.add(domain)
    checker = FakeChecker()
    checker.results.append(make_ok_result(domain.name, EXPIRES))
    pub = FakePublisher()
    sleeps, sleeper = _zero_sleep_factory()
    use_case = _build(repo=repo, checker=checker, pub=pub, sleeper=sleeper)

    result = await use_case.execute(domain.name)

    assert result.outcome is CheckOutcome.OK
    assert result.expires_at == EXPIRES
    saved = repo.store[domain.name.value]
    assert saved.last_check is not None
    assert saved.last_check.expires_at == EXPIRES
    assert any(isinstance(e, DomainCheckCompleted) for e in pub.events)
    assert sleeps == []


async def test_transient_error_then_success() -> None:
    repo = FakeMonitoredRepo()
    domain = _domain()
    await repo.add(domain)
    checker = FakeChecker()
    checker.results.extend(
        [
            make_transient_result(domain.name),
            make_ok_result(domain.name, EXPIRES),
        ]
    )
    pub = FakePublisher()
    sleeps, sleeper = _zero_sleep_factory()
    use_case = _build(repo=repo, checker=checker, pub=pub, sleeper=sleeper)

    result = await use_case.execute(domain.name)

    assert result.outcome is CheckOutcome.OK
    assert sleeps == [1]  # first retry waits one second per default policy
    assert sum(isinstance(e, DomainCheckCompleted) for e in pub.events) == 1


async def test_transient_exhausts_retries() -> None:
    repo = FakeMonitoredRepo()
    domain = _domain()
    await repo.add(domain)
    checker = FakeChecker()
    checker.exceptions.extend([TransientCheckError("nope")] * 3)
    pub = FakePublisher()
    sleeps, sleeper = _zero_sleep_factory()
    use_case = _build(repo=repo, checker=checker, pub=pub, sleeper=sleeper)

    with pytest.raises(TransientCheckError):
        await use_case.execute(domain.name)

    failed = [e for e in pub.events if isinstance(e, DomainCheckFailed)]
    assert len(failed) == 1
    assert failed[0].transient is True
    assert sleeps == [1, 5]


async def test_permanent_error_no_retry() -> None:
    repo = FakeMonitoredRepo()
    domain = _domain()
    await repo.add(domain)
    checker = FakeChecker()
    checker.exceptions.append(PermanentCheckError("nx_domain"))
    pub = FakePublisher()
    _, sleeper = _zero_sleep_factory()
    use_case = _build(repo=repo, checker=checker, pub=pub, sleeper=sleeper)

    with pytest.raises(PermanentCheckError):
        await use_case.execute(domain.name)
    failed = [e for e in pub.events if isinstance(e, DomainCheckFailed)]
    assert len(failed) == 1
    assert failed[0].transient is False
    assert len(checker.calls) == 1


async def test_permanent_check_outcome_no_retry() -> None:
    repo = FakeMonitoredRepo()
    domain = _domain()
    await repo.add(domain)
    checker = FakeChecker()
    checker.results.append(make_permanent_result(domain.name))
    pub = FakePublisher()
    _, sleeper = _zero_sleep_factory()
    use_case = _build(repo=repo, checker=checker, pub=pub, sleeper=sleeper)

    result = await use_case.execute(domain.name)
    assert result.outcome is CheckOutcome.PERMANENT_ERROR
    failed = [e for e in pub.events if isinstance(e, DomainCheckFailed)]
    assert len(failed) == 1


async def test_missing_domain_raises() -> None:
    repo = FakeMonitoredRepo()
    checker = FakeChecker()
    pub = FakePublisher()
    _, sleeper = _zero_sleep_factory()
    use_case = _build(repo=repo, checker=checker, pub=pub, sleeper=sleeper)
    with pytest.raises(DomainNotMonitoredError):
        await use_case.execute(DomainName("ghost.com"))


async def test_missing_checker_raises() -> None:
    repo = FakeMonitoredRepo()
    domain = _domain()
    await repo.add(domain)
    pub = FakePublisher()
    _, sleeper = _zero_sleep_factory()
    use_case = CheckDomainUseCase(
        repo=repo,
        checkers={},  # empty registry
        retry_policy=RetryPolicy(),
        publisher=pub,
        clock=FixedClock(NOW),
        sleeper=sleeper,
    )
    with pytest.raises(CheckerNotRegisteredError):
        await use_case.execute(domain.name)


_ = (timedelta, ClassVar)
