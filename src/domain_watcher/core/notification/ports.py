"""Ports (Protocols) for the notification context (ADR 0002 §5)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.monitoring.value_objects import ChannelId
    from domain_watcher.core.notification.entities import Alert, Channel
    from domain_watcher.core.shared.value_objects import DomainName, Duration


@runtime_checkable
class Notifier(Protocol):
    """Sends one ``Alert`` over one ``Channel``.

    Adapters raise ``DeliveryFailedError`` on transport failure (retryable).
    Permanent transport failures (e.g. invalid token) raise the bare
    ``NotificationError`` so the dispatcher does not retry.
    """

    id: ClassVar[str]

    async def send(self, alert: Alert, channel: Channel) -> None: ...


@runtime_checkable
class ChannelResolver(Protocol):
    """Resolves a ``MonitoredDomain`` to the channels that should receive its alerts.

    Default ``StaticChannelResolver`` (in ``application/``) returns one
    ``Channel`` per id in ``domain.channels``, looked up via the notifier
    registry. The bot ships a tenant-aware impl that returns one ``Channel``
    per active subscriber.
    """

    async def channels_for(self, domain: MonitoredDomain) -> Sequence[Channel]: ...


@runtime_checkable
class IdempotencyStore(Protocol):
    """Stops us paging the operator every 6h for a week straight (ADR 0002 §5).

    Keyed by ``(domain, threshold, cycle_id, channel)``. ``cycle_id`` is
    derived from the current expiration date — a renewal yields a fresh
    cycle so alerts re-fire for the next cycle automatically.
    """

    async def already_fired(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
    ) -> bool: ...

    async def record(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
        at: datetime,
    ) -> None: ...


__all__ = ["ChannelResolver", "IdempotencyStore", "Notifier"]
