"""Public re-export of every concrete adapter shipped with ``domain_watcher``.

This module is the stable surface for embedded callers and the bot repo:

    from domain_watcher.adapters import RdapChecker, TelegramNotifier, ...

It MUST NOT re-export anything from ``domain_watcher.core`` — adapters
are infrastructure, callers wire them by importing them here. Tests
enforce this with a directed ``import-linter`` reverse contract plus a
runtime ``pkgutil`` walk in ``tests/unit/test_public_api.py``.

``WhoisChecker`` is the public id ``"whois"`` composite adapter
(``WhoisCheckerWithParser``); the internal raw fetcher (``_WhoisFetcher``)
is intentionally not exported.
"""

from __future__ import annotations

# Checkers
from domain_watcher.infrastructure.checkers._whois_with_parser import (
    WhoisCheckerWithParser as WhoisChecker,
)
from domain_watcher.infrastructure.checkers.rdap import RdapChecker
from domain_watcher.infrastructure.checkers.script import ScriptChecker

# Notifiers
from domain_watcher.infrastructure.notifiers.discord import DiscordNotifier
from domain_watcher.infrastructure.notifiers.email_smtp import EmailNotifier
from domain_watcher.infrastructure.notifiers.telegram import TelegramNotifier
from domain_watcher.infrastructure.notifiers.webhook import WebhookNotifier

# Parsers
from domain_watcher.infrastructure.parsers.llm_suggester import LiteLLMRuleSuggester
from domain_watcher.infrastructure.parsers.regex import RegexWhoisParser

# Persistence (memory + SQL)
from domain_watcher.infrastructure.persistence.memory.idempotency import (
    MemoryIdempotencyStore,
)
from domain_watcher.infrastructure.persistence.memory.learned_rules import (
    MemoryLearnedRulesRepo,
)
from domain_watcher.infrastructure.persistence.memory.monitored import (
    MemoryMonitoredDomainRepo,
)
from domain_watcher.infrastructure.persistence.sql.repos.idempotency import (
    SqlIdempotencyStore,
)
from domain_watcher.infrastructure.persistence.sql.repos.learned_rules import (
    SqlLearnedRulesRepo,
)
from domain_watcher.infrastructure.persistence.sql.repos.monitored import (
    SqlMonitoredDomainRepo,
)

# Scheduling
from domain_watcher.infrastructure.scheduling.apscheduler import ApsScheduler

__all__ = [
    "ApsScheduler",
    "DiscordNotifier",
    "EmailNotifier",
    "LiteLLMRuleSuggester",
    "MemoryIdempotencyStore",
    "MemoryLearnedRulesRepo",
    "MemoryMonitoredDomainRepo",
    "RdapChecker",
    "RegexWhoisParser",
    "ScriptChecker",
    "SqlIdempotencyStore",
    "SqlLearnedRulesRepo",
    "SqlMonitoredDomainRepo",
    "TelegramNotifier",
    "WebhookNotifier",
    "WhoisChecker",
]
