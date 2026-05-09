from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.monitoring.value_objects import LastCheck
from domain_watcher.core.notification.entities import AlertSeverity
from domain_watcher.core.notification.policies import NotificationPolicy
from domain_watcher.core.shared.value_objects import DomainName, Duration

THRESHOLDS = (Duration.days(30), Duration.days(7), Duration.days(1))


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def _ok_result(domain: DomainName, expires_at: datetime) -> CheckResult:
    return CheckResult(
        domain=domain,
        outcome=CheckOutcome.OK,
        expires_at=expires_at,
        source="rdap",
    )


def _expected_cycle_id(at: datetime) -> str:
    return hashlib.sha256(at.isoformat().encode()).hexdigest()[:16]


def test_no_prev_threshold_just_crossed() -> None:
    # expires_at exactly 30 days out: 30d threshold is crossed (time_left == 30d).
    domain = DomainName("example.com")
    expires = _now() + timedelta(days=30)
    policy = NotificationPolicy(thresholds=THRESHOLDS)
    alerts = policy.alerts_for(
        previous=None,
        current=_ok_result(domain, expires),
        now=_now(),
    )
    assert len(alerts) == 1
    a = alerts[0]
    assert a.threshold == Duration.days(30)
    assert a.severity is AlertSeverity.INFO
    assert a.domain == domain
    assert a.expires_at == expires
    assert a.cycle_id == _expected_cycle_id(expires)


def test_no_prev_no_threshold_crossed() -> None:
    # 31 days out: nothing crossed.
    expires = _now() + timedelta(days=31)
    policy = NotificationPolicy(thresholds=THRESHOLDS)
    alerts = policy.alerts_for(
        previous=None,
        current=_ok_result(DomainName("example.com"), expires),
        now=_now(),
    )
    assert alerts == ()


def test_no_renewal_no_new_crossing() -> None:
    # prev already inside the 30d window; current still inside 30d; same cycle → no alert.
    domain = DomainName("example.com")
    expires = _now() + timedelta(days=20)
    prev = LastCheck(
        at=_now() - timedelta(hours=6),
        outcome=CheckOutcome.OK,
        expires_at=expires,
    )
    current = _ok_result(domain, expires)
    policy = NotificationPolicy(thresholds=THRESHOLDS)
    alerts = policy.alerts_for(previous=prev, current=current, now=_now())
    assert alerts == ()


def test_renewal_re_fires_threshold_with_new_cycle_id() -> None:
    """ADR 0002: a renewal yields a fresh cycle_id; thresholds re-fire."""
    domain = DomainName("example.com")
    old_expires = _now() - timedelta(days=1) + timedelta(days=20)
    new_expires = old_expires + timedelta(days=365)
    # Renewed to ~12 months out, but we look LATER when the new cycle's 30d
    # threshold is also crossed.
    look_at = new_expires - timedelta(days=20)
    prev = LastCheck(
        at=look_at - timedelta(days=1),
        outcome=CheckOutcome.OK,
        expires_at=old_expires,
    )
    current = _ok_result(domain, new_expires)
    policy = NotificationPolicy(thresholds=THRESHOLDS)
    alerts = policy.alerts_for(previous=prev, current=current, now=look_at)
    # In the new cycle, 30d is crossed (time_left == 20d) → alert.
    thresholds_emitted = {a.threshold for a in alerts}
    assert Duration.days(30) in thresholds_emitted
    cids = {a.cycle_id for a in alerts}
    assert cids == {_expected_cycle_id(new_expires)}
    assert _expected_cycle_id(new_expires) != _expected_cycle_id(old_expires)


def test_non_ok_current_yields_no_alerts() -> None:
    domain = DomainName("example.com")
    current = CheckResult(
        domain=domain,
        outcome=CheckOutcome.TRANSIENT_ERROR,
        expires_at=None,
        source="rdap",
        error="timeout",
    )
    policy = NotificationPolicy(thresholds=THRESHOLDS)
    assert policy.alerts_for(previous=None, current=current, now=_now()) == ()


def test_thresholds_evaluated_descending() -> None:
    # 0.5 days out: all three thresholds crossed.
    domain = DomainName("example.com")
    expires = _now() + timedelta(hours=12)
    policy = NotificationPolicy(thresholds=THRESHOLDS)
    alerts = policy.alerts_for(
        previous=None,
        current=_ok_result(domain, expires),
        now=_now(),
    )
    # Returned in descending threshold order.
    assert [a.threshold for a in alerts] == list(THRESHOLDS)
    severities = [a.severity for a in alerts]
    assert severities == [AlertSeverity.INFO, AlertSeverity.WARNING, AlertSeverity.CRITICAL]


def test_alerts_for_pure_no_idempotency_consult() -> None:
    """Policy is pure: even after generating alerts repeatedly with the same
    inputs, output is identical (no hidden state)."""
    domain = DomainName("example.com")
    expires = _now() + timedelta(days=30)
    policy = NotificationPolicy(thresholds=THRESHOLDS)
    a = policy.alerts_for(previous=None, current=_ok_result(domain, expires), now=_now())
    b = policy.alerts_for(previous=None, current=_ok_result(domain, expires), now=_now())
    assert a == b


def test_severity_mapping_default() -> None:
    domain = DomainName("example.com")
    policy = NotificationPolicy(thresholds=(Duration.days(30), Duration.days(7), Duration.days(1)))
    # 0.5 days → all three thresholds crossed → 30d INFO, 7d WARNING, 1d CRITICAL.
    alerts = policy.alerts_for(
        previous=None,
        current=_ok_result(domain, _now() + timedelta(hours=12)),
        now=_now(),
    )
    by_thr = {a.threshold: a.severity for a in alerts}
    assert by_thr[Duration.days(30)] is AlertSeverity.INFO
    assert by_thr[Duration.days(7)] is AlertSeverity.WARNING
    assert by_thr[Duration.days(1)] is AlertSeverity.CRITICAL


def test_empty_thresholds_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        NotificationPolicy(thresholds=())
