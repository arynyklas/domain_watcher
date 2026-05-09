"""Tests for the error hierarchy defined in ADR 0002 §1."""

from __future__ import annotations

import pytest

from domain_watcher.core.shared.errors import (
    CheckingError,
    ConfigError,
    DeliveryFailedError,
    DomainWatcherError,
    NoMatchingRuleError,
    NotificationError,
    ParseError,
    PermanentCheckError,
    RuleValidationError,
    SuggestionError,
    TransientCheckError,
)


def test_root_exception() -> None:
    assert issubclass(DomainWatcherError, Exception)


def test_config_error_is_root() -> None:
    assert issubclass(ConfigError, DomainWatcherError)


def test_checking_hierarchy() -> None:
    assert issubclass(CheckingError, DomainWatcherError)
    assert issubclass(TransientCheckError, CheckingError)
    assert issubclass(PermanentCheckError, CheckingError)


def test_parse_hierarchy() -> None:
    assert issubclass(ParseError, DomainWatcherError)
    assert issubclass(NoMatchingRuleError, ParseError)
    assert issubclass(SuggestionError, ParseError)
    assert issubclass(RuleValidationError, ParseError)


def test_notification_hierarchy() -> None:
    assert issubclass(NotificationError, DomainWatcherError)
    assert issubclass(DeliveryFailedError, NotificationError)


def test_transient_check_error_is_raisable() -> None:
    with pytest.raises(TransientCheckError, match="boom"):
        raise TransientCheckError("boom")


def test_permanent_check_caught_as_checking() -> None:
    with pytest.raises(CheckingError):
        raise PermanentCheckError("nope")


def test_rule_validation_caught_as_parse() -> None:
    with pytest.raises(ParseError):
        raise RuleValidationError("bad regex")


def test_suggestion_error_carries_transient_flag() -> None:
    err = SuggestionError("timeout", transient=True)
    assert err.transient is True
    assert err.permanent is False


def test_suggestion_error_carries_permanent_flag() -> None:
    err = SuggestionError("auth failure", permanent=True)
    assert err.permanent is True
    assert err.transient is False


def test_delivery_failed_caught_as_notification() -> None:
    with pytest.raises(NotificationError):
        raise DeliveryFailedError("timeout")
