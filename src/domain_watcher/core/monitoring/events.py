"""Events emitted by the monitoring bounded context (ADR 0002 §2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain_watcher.core.shared.events import DomainEvent

if TYPE_CHECKING:
    from domain_watcher.core.shared.value_objects import DomainName


@dataclass(frozen=True, slots=True)
class DomainAdded(DomainEvent):
    """A new ``MonitoredDomain`` was added to the repository."""

    domain: DomainName | None = None

    def __post_init__(self) -> None:
        if self.domain is None:
            raise ValueError("DomainAdded.domain is required")


@dataclass(frozen=True, slots=True)
class DomainRemoved(DomainEvent):
    """A ``MonitoredDomain`` was removed."""

    domain: DomainName | None = None

    def __post_init__(self) -> None:
        if self.domain is None:
            raise ValueError("DomainRemoved.domain is required")


@dataclass(frozen=True, slots=True)
class DomainCheckRequested(DomainEvent):
    """A check has been scheduled / requested for a domain."""

    domain: DomainName | None = None
    checker_id: str = ""

    def __post_init__(self) -> None:
        if self.domain is None:
            raise ValueError("DomainCheckRequested.domain is required")
        if not self.checker_id:
            raise ValueError("DomainCheckRequested.checker_id is required")


__all__ = ["DomainAdded", "DomainCheckRequested", "DomainRemoved"]
