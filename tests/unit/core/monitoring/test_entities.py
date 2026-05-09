from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import (
    ChannelId,
    CheckSchedule,
    LastCheck,
)
from domain_watcher.core.shared.value_objects import DomainName, Duration


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def _domain(
    *,
    last_check: LastCheck | None = None,
    thresholds: tuple[Duration, ...] = (Duration.days(30), Duration.days(7), Duration.days(1)),
    channels: tuple[ChannelId, ...] = (ChannelId("tg-ops"),),
) -> MonitoredDomain:
    return MonitoredDomain(
        name=DomainName("example.com"),
        schedule=CheckSchedule("0 */6 * * *"),
        checker_id="rdap",
        notify_thresholds=thresholds,
        channels=channels,
        last_check=last_check,
    )


def test_basic_construction() -> None:
    d = _domain()
    assert d.name.value == "example.com"
    assert d.checker_id == "rdap"


def test_empty_thresholds_rejected() -> None:
    with pytest.raises(ValueError, match="thresholds"):
        _domain(thresholds=())


def test_thresholds_must_be_strictly_descending() -> None:
    with pytest.raises(ValueError, match="descending"):
        _domain(thresholds=(Duration.days(7), Duration.days(7)))
    with pytest.raises(ValueError, match="descending"):
        _domain(thresholds=(Duration.days(1), Duration.days(7)))


def test_empty_channels_rejected() -> None:
    with pytest.raises(ValueError, match="channels"):
        _domain(channels=())


def test_with_check_result_returns_new_instance() -> None:
    d = _domain()
    r = CheckResult(
        domain=d.name,
        outcome=CheckOutcome.OK,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        source="rdap",
    )
    d2 = d.with_check_result(r, at=_now())
    assert d2 is not d
    assert d2.last_check is not None
    assert d2.last_check.at == _now()
    assert d2.last_check.outcome is CheckOutcome.OK
    assert d2.last_check.expires_at == datetime(2027, 1, 1, tzinfo=UTC)


def test_with_check_result_monotonic() -> None:
    earlier = LastCheck(
        at=_now(),
        outcome=CheckOutcome.OK,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    d = _domain(last_check=earlier)
    r = CheckResult(
        domain=d.name,
        outcome=CheckOutcome.OK,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        source="rdap",
    )
    # Going backwards is rejected.
    with pytest.raises(ValueError, match="monotonic"):
        d.with_check_result(r, at=_now() - timedelta(seconds=1))
    # Same moment is allowed (idempotent re-check).
    d.with_check_result(r, at=_now())


def test_is_due_when_last_check_none(monkeypatch: pytest.MonkeyPatch) -> None:
    d = _domain()
    assert d.is_due(_now()) is True


def test_is_due_after_six_hours() -> None:
    earlier = LastCheck(
        at=_now() - timedelta(hours=6, minutes=1),
        outcome=CheckOutcome.OK,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    d = _domain(last_check=earlier)
    assert d.is_due(_now()) is True


def test_is_not_due_immediately_after_check() -> None:
    earlier = LastCheck(
        at=_now() - timedelta(minutes=5),
        outcome=CheckOutcome.OK,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    d = _domain(last_check=earlier)
    # CheckSchedule "0 */6 * * *" => check at minute 0 of every 6th hour.
    assert d.is_due(_now()) is False


def test_metadata_immutable_default() -> None:
    d = _domain()
    assert dict(d.metadata) == {}
