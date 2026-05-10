"""Public test harness for ``domain_watcher`` plugins.

This module is part of the **stable** public surface — semver applies just
like the runtime API. Plugin authors should depend on it and write a
TestCase that subclasses one of the contract bases under
:mod:`domain_watcher.testing.contract`.

Re-exported helpers:

* :class:`FixedClock` — deterministic clock for tests.
* :class:`MemoryMonitoredDomainRepo`, :class:`MemoryLearnedRulesRepo`,
  :class:`MemoryIdempotencyStore` — in-memory repos with the same contract
  as the SQL impls.
* :class:`PluginContractTest` (notifier), :class:`CheckerContractTest`,
  :class:`RepoContractTest` — base classes that run the conformance suite
  defined in ADRs 0002 / 0004 against a plugin author's adapter.

Usage example::

    from domain_watcher.testing import PluginContractTest

    class TestMyNotifier(PluginContractTest):
        def make_ok(self) -> Notifier:
            return MyNotifier(...)

        def make_failing(self) -> Notifier:
            return MyNotifier(broken_endpoint=True, ...)
"""

from __future__ import annotations

from domain_watcher.testing.clocks import FixedClock
from domain_watcher.testing.contract.checker import CheckerContractTest
from domain_watcher.testing.contract.notifier import PluginContractTest
from domain_watcher.testing.contract.repo import RepoContractTest
from domain_watcher.testing.repos import (
    MemoryIdempotencyStore,
    MemoryLearnedRulesRepo,
    MemoryMonitoredDomainRepo,
)

__all__ = [
    "CheckerContractTest",
    "FixedClock",
    "MemoryIdempotencyStore",
    "MemoryLearnedRulesRepo",
    "MemoryMonitoredDomainRepo",
    "PluginContractTest",
    "RepoContractTest",
]
