"""Trivial Notifier-shaped class registered via entry points."""

from __future__ import annotations

from typing import Any


class FakeNotifier:
    """Conforms to the :class:`domain_watcher.core.notification.ports.Notifier` shape.

    The body is intentionally minimal — discovery is what's under test, not
    delivery semantics.
    """

    id = "fake"

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or {}

    async def send(self, alert: object, channel: object) -> None:  # pragma: no cover
        return None
