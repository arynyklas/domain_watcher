from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.shared.value_objects import DomainName


def _domain() -> DomainName:
    return DomainName("example.com")


def test_check_outcome_values() -> None:
    assert CheckOutcome.OK == "ok"
    assert CheckOutcome.TRANSIENT_ERROR == "transient_error"
    assert CheckOutcome.PERMANENT_ERROR == "permanent_error"


def test_ok_with_expires_at_is_valid() -> None:
    r = CheckResult(
        domain=_domain(),
        outcome=CheckOutcome.OK,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        source="rdap",
    )
    assert r.outcome is CheckOutcome.OK
    assert r.expires_at is not None


def test_ok_without_expires_at_rejected() -> None:
    with pytest.raises(ValueError, match="OK"):
        CheckResult(
            domain=_domain(),
            outcome=CheckOutcome.OK,
            expires_at=None,
            source="rdap",
        )


def test_error_outcome_without_message_rejected() -> None:
    with pytest.raises(ValueError, match="error"):
        CheckResult(
            domain=_domain(),
            outcome=CheckOutcome.PERMANENT_ERROR,
            expires_at=None,
            source="rdap",
            error=None,
        )


def test_error_with_expires_at_rejected() -> None:
    # If outcome is non-OK, expires_at must be None.
    with pytest.raises(ValueError, match="OK"):
        CheckResult(
            domain=_domain(),
            outcome=CheckOutcome.TRANSIENT_ERROR,
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            source="rdap",
            error="timeout",
        )


def test_transient_error_with_message_valid() -> None:
    r = CheckResult(
        domain=_domain(),
        outcome=CheckOutcome.TRANSIENT_ERROR,
        expires_at=None,
        source="rdap",
        error="connection reset",
    )
    assert r.outcome is CheckOutcome.TRANSIENT_ERROR
    assert r.error == "connection reset"


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        CheckResult(
            domain=_domain(),
            outcome=CheckOutcome.OK,
            expires_at=datetime(2027, 1, 1),  # naive
            source="rdap",
        )
