"""Cross-context ``DomainEvent`` base (ADR 0002 §6).

Lives in ``core/shared`` rather than under any one bounded context: every
context defines its own subclasses (DomainCheckCompleted, ParseFailed, …)
that ride this base shape. The application-layer event bus reads
``criticality`` to choose the queue tier.

Note: the plan literally places ``DomainEvent`` under ``monitoring/events.py``;
we host it under ``shared/events.py`` instead so that ``checking/``,
``parsing/``, and ``notification/`` can import it without one bounded
context becoming a sibling's dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

if TYPE_CHECKING:
    from datetime import datetime

Criticality = Literal["critical", "standard"]


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base for every domain event in ``core/``.

    Subclasses extend with their own payload fields and override
    ``criticality`` when loss is unacceptable. The two-tier event bus uses
    the ``criticality`` ClassVar to choose unbounded vs. bounded queues.
    """

    occurred_at: datetime
    correlation_id: str = field(default="")
    """ULID-shaped string. Empty default keeps construction terse in tests;
    real call sites stamp a fresh id at the use-case boundary."""

    criticality: ClassVar[Criticality] = "standard"


class EventPublisher(Protocol):
    """Ports an in-process or out-of-process event bus.

    Adapters (e.g. the in-process bus in ``application/event_bus.py``)
    implement ``publish``. Critical events MUST NOT be silently dropped.
    """

    async def publish(self, event: DomainEvent) -> None: ...


__all__ = ["Criticality", "DomainEvent", "EventPublisher"]
