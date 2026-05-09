"""Entities and severity enum for the notification context (ADR 0002 §5)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from domain_watcher.core.monitoring.value_objects import ChannelId
    from domain_watcher.core.shared.value_objects import DomainName, Duration


_CYCLE_ID_RE = re.compile(r"^[0-9a-f]{16}$")


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Channel:
    """A configured destination for alerts.

    ``routing`` is a per-recipient address payload (e.g. ``{"chat_id": "123"}``).
    Adapters validate its shape on first send. Construction-time secrets
    (transport tokens, SMTP passwords) live in ``NotifierConfig.settings``,
    NEVER on ``Channel.routing``: ADR 0002 §5 calls this out as a contract.
    """

    id: ChannelId
    notifier_id: str
    routing: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.notifier_id, str) or not self.notifier_id.strip():
            raise ValueError("Channel.notifier_id is required")


@dataclass(frozen=True, slots=True)
class Alert:
    """A single threshold-crossing alert that needs delivering."""

    domain: DomainName
    expires_at: datetime
    threshold: Duration
    severity: AlertSeverity
    cycle_id: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        tz = self.expires_at.tzinfo
        if tz is None or tz.utcoffset(self.expires_at) is None:
            raise ValueError("Alert.expires_at must be tz-aware UTC")
        if not _CYCLE_ID_RE.match(self.cycle_id):
            raise ValueError(
                "Alert.cycle_id must be 16 lowercase hex characters "
                "(sha256(expires_at.isoformat())[:16])"
            )


__all__ = ["Alert", "AlertSeverity", "Channel"]
