"""Error hierarchy for ``domain_watcher`` (ADR 0002 §1).

``core/`` never raises stdlib ``Exception`` directly; adapters wrap their
failures in these. Catching ``DomainWatcherError`` at a boundary catches
everything we throw on purpose; bare ``Exception`` from inside core/app
indicates a bug.
"""

from __future__ import annotations


class DomainWatcherError(Exception):
    """Root of the project-specific exception hierarchy."""


class ConfigError(DomainWatcherError):
    """Raised when configuration cannot be loaded or fails validation."""


class CheckingError(DomainWatcherError):
    """Raised when a checker cannot determine an expiration date."""


class TransientCheckError(CheckingError):
    """Network blip / 5xx / connection reset — retry under RetryPolicy."""


class PermanentCheckError(CheckingError):
    """No-such-domain / authoritative refusal — surface, do not retry."""


class ParseError(DomainWatcherError):
    """Raised when WHOIS text cannot be parsed into a date."""


class NoMatchingRuleError(ParseError):
    """No static or learned ``ParseRule`` matched the WHOIS text."""


class SuggestionError(ParseError):
    """Runtime ``RuleSuggester`` could not produce a candidate rule.

    ``transient=True`` indicates a retry is appropriate (timeout, 5xx,
    network reset). ``permanent=True`` indicates an unrecoverable failure
    for this attempt (auth failure, malformed model output that cannot
    pass validation). Default flags are both ``False`` — the caller treats
    it as a generic suggestion failure.
    """

    def __init__(
        self,
        message: str,
        *,
        transient: bool = False,
        permanent: bool = False,
    ) -> None:
        super().__init__(message)
        self.transient = transient
        self.permanent = permanent


class RuleValidationError(ParseError):
    """A candidate rule was rejected by the validation pipeline (ADR 0006 §4)."""


class NotificationError(DomainWatcherError):
    """Raised when an alert cannot be delivered."""


class DeliveryFailedError(NotificationError):
    """Transport-level failure during alert delivery; retryable by policy."""


__all__ = [
    "CheckingError",
    "ConfigError",
    "DeliveryFailedError",
    "DomainWatcherError",
    "NoMatchingRuleError",
    "NotificationError",
    "ParseError",
    "PermanentCheckError",
    "RuleValidationError",
    "SuggestionError",
    "TransientCheckError",
]
