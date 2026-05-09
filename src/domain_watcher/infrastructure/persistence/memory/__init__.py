"""In-memory persistence adapters."""

from __future__ import annotations

from domain_watcher.infrastructure.persistence.memory.idempotency import (
    MemoryIdempotencyStore,
)
from domain_watcher.infrastructure.persistence.memory.learned_rules import (
    MemoryLearnedRulesRepo,
)
from domain_watcher.infrastructure.persistence.memory.monitored import (
    MemoryMonitoredDomainRepo,
)

__all__ = [
    "MemoryIdempotencyStore",
    "MemoryLearnedRulesRepo",
    "MemoryMonitoredDomainRepo",
]
