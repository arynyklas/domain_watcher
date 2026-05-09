"""Events emitted by the notification dispatcher (ADR 0002 §5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from domain_watcher.core.shared.events import DomainEvent

if TYPE_CHECKING:
    from domain_watcher.core.monitoring.value_objects import ChannelId
    from domain_watcher.core.notification.entities import Alert
    from domain_watcher.core.shared.events import Criticality


@dataclass(frozen=True, slots=True)
class NotificationDispatched(DomainEvent):
    alert: Alert | None = None
    channel: ChannelId | None = None

    def __post_init__(self) -> None:
        if self.alert is None:
            raise ValueError("NotificationDispatched.alert is required")
        if self.channel is None:
            raise ValueError("NotificationDispatched.channel is required")


@dataclass(frozen=True, slots=True)
class NotificationFailed(DomainEvent):
    alert: Alert | None = None
    channel: ChannelId | None = None
    reason: str = ""
    attempts: int = 0

    criticality: ClassVar[Criticality] = "critical"

    def __post_init__(self) -> None:
        if self.alert is None:
            raise ValueError("NotificationFailed.alert is required")
        if self.channel is None:
            raise ValueError("NotificationFailed.channel is required")
        if not self.reason:
            raise ValueError("NotificationFailed.reason is required")
        if self.attempts < 1:
            raise ValueError("NotificationFailed.attempts must be >= 1")


__all__ = ["NotificationDispatched", "NotificationFailed"]
