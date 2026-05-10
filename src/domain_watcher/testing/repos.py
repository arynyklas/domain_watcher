"""Stable re-export of in-memory repository adapters.

These satisfy the same ports as the SQL adapters and are intentionally
exposed under :mod:`domain_watcher.testing` so plugin authors can wire
them into a ``DomainWatcher`` for deterministic tests without touching
the (private) ``infrastructure/persistence/memory`` module path.
"""

from __future__ import annotations

from domain_watcher.infrastructure.persistence.memory import (
    MemoryIdempotencyStore,
    MemoryLearnedRulesRepo,
    MemoryMonitoredDomainRepo,
)

__all__ = [
    "MemoryIdempotencyStore",
    "MemoryLearnedRulesRepo",
    "MemoryMonitoredDomainRepo",
]
