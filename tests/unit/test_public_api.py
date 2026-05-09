"""Public-API freeze: every name listed in plan Task 9.2 must import cleanly.

Adds a directional check: nothing under ``domain_watcher.adapters``
re-exports a symbol that lives in ``domain_watcher.core``. This is the
runtime backstop for the import-linter contract.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil


def test_top_level_imports_are_stable() -> None:
    # If any of these names disappear, downstream integrators break.
    from domain_watcher import (  # noqa: F401
        Alert,
        AlertSeverity,
        ChannelId,
        CheckOutcome,
        CheckResult,
        CheckSchedule,
        ConfigError,
        DomainCheckCompleted,
        DomainCheckFailed,
        DomainEvent,
        DomainName,
        DomainWatcher,
        DomainWatcherBuilder,
        DomainWatcherError,
        Duration,
        NotificationDispatched,
        NotificationError,
        NotificationFailed,
        ParseError,
        ParseFailed,
        WhoisRuleInvalidated,
        WhoisRuleLearned,
        WhoisRuleRevalidated,
        __version__,
    )

    assert isinstance(__version__, str) and __version__


def test_adapters_exports_are_stable() -> None:
    from domain_watcher.adapters import (  # noqa: F401
        ApsScheduler,
        DiscordNotifier,
        EmailNotifier,
        LiteLLMRuleSuggester,
        MemoryIdempotencyStore,
        MemoryLearnedRulesRepo,
        MemoryMonitoredDomainRepo,
        RdapChecker,
        RegexWhoisParser,
        ScriptChecker,
        SqlIdempotencyStore,
        SqlLearnedRulesRepo,
        SqlMonitoredDomainRepo,
        TelegramNotifier,
        WebhookNotifier,
        WhoisChecker,
    )


def test_adapters_module_does_not_re_export_core() -> None:
    """Adapters MUST NOT re-export anything whose ``__module__`` is in core."""
    import domain_watcher.adapters as adapters

    for name in adapters.__all__:
        obj = getattr(adapters, name)
        module = getattr(obj, "__module__", "")
        assert not module.startswith("domain_watcher.core"), (
            f"adapter re-export {name!r} (from {module}) leaks core into the adapters surface"
        )


def test_core_subpackages_define_no_third_party_imports() -> None:
    """Walk every ``domain_watcher.core.*`` module and verify it imports
    only stdlib + ``typing_extensions`` + first-party names.
    """
    import domain_watcher.core as core_pkg

    forbidden = {"httpx", "pydantic", "sqlalchemy", "apscheduler", "watchdog", "typer", "structlog"}
    for module_info in pkgutil.walk_packages(core_pkg.__path__, prefix="domain_watcher.core."):
        module = importlib.import_module(module_info.name)
        for name, value in inspect.getmembers(module):
            if not inspect.ismodule(value):
                continue
            top = value.__name__.split(".")[0]
            assert top not in forbidden, (
                f"core module {module.__name__} imports forbidden top-level "
                f"package {top!r} as {name}"
            )
