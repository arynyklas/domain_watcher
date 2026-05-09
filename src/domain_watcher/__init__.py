"""Public API surface (Task 9.2).

Importable names are stable across patch releases. The bot repo (out of
scope) and any third-party integrator depends on this surface. Concrete
adapters live under ``domain_watcher.adapters``; the test harness lives
under ``domain_watcher.testing`` (Phase 11).
"""

from __future__ import annotations

# Core value objects + entities the integrator routinely passes around.
from domain_watcher.core.checking.events import (
    DomainCheckCompleted,
    DomainCheckFailed,
)
from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.monitoring.value_objects import (
    ChannelId,
    CheckSchedule,
)
from domain_watcher.core.notification.entities import Alert, AlertSeverity
from domain_watcher.core.notification.events import (
    NotificationDispatched,
    NotificationFailed,
)
from domain_watcher.core.parsing.events import (
    ParseFailed,
    WhoisRuleInvalidated,
    WhoisRuleLearned,
    WhoisRuleRevalidated,
)
from domain_watcher.core.shared.errors import (
    ConfigError,
    DomainWatcherError,
    NotificationError,
    ParseError,
)
from domain_watcher.core.shared.events import DomainEvent
from domain_watcher.core.shared.value_objects import DomainName, Duration

# Library façade.
from domain_watcher.interfaces.library.api import DomainWatcher, DomainWatcherBuilder

__version__ = "0.1.0"

__all__ = [
    "Alert",
    "AlertSeverity",
    "ChannelId",
    "CheckOutcome",
    "CheckResult",
    "CheckSchedule",
    "ConfigError",
    "DomainCheckCompleted",
    "DomainCheckFailed",
    "DomainEvent",
    "DomainName",
    "DomainWatcher",
    "DomainWatcherBuilder",
    "DomainWatcherError",
    "Duration",
    "NotificationDispatched",
    "NotificationError",
    "NotificationFailed",
    "ParseError",
    "ParseFailed",
    "WhoisRuleInvalidated",
    "WhoisRuleLearned",
    "WhoisRuleRevalidated",
    "__version__",
]
