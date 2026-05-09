"""Events emitted by checking use cases (ADR 0002 §3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from domain_watcher.core.shared.events import DomainEvent

if TYPE_CHECKING:
    from domain_watcher.core.checking.value_objects import CheckResult
    from domain_watcher.core.shared.events import Criticality
    from domain_watcher.core.shared.value_objects import DomainName


@dataclass(frozen=True, slots=True)
class DomainCheckCompleted(DomainEvent):
    """A check finished and produced a ``CheckResult``."""

    # Defaults required because the base has fields with defaults; callers
    # always pass `result` explicitly. None is rejected in __post_init__.
    result: CheckResult | None = None

    def __post_init__(self) -> None:
        if self.result is None:
            raise ValueError("DomainCheckCompleted.result is required")


@dataclass(frozen=True, slots=True)
class DomainCheckFailed(DomainEvent):
    """A check exhausted retries or hit a permanent error."""

    domain: DomainName | None = None
    source: str = ""
    reason: str = ""
    transient: bool = False

    criticality: ClassVar[Criticality] = "critical"

    def __post_init__(self) -> None:
        if self.domain is None:
            raise ValueError("DomainCheckFailed.domain is required")
        if not self.reason:
            raise ValueError("DomainCheckFailed.reason is required")


__all__ = ["DomainCheckCompleted", "DomainCheckFailed"]
