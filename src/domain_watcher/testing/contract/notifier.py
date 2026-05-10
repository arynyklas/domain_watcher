"""Notifier conformance suite (ADR 0004 §10).

Plugin authors subclass :class:`PluginContractTest`, implement
:meth:`make_ok` and :meth:`make_failing`, and pytest will discover and
run every contract test method against their adapter. The base class
itself is opt-out for collection (its name does not start with
``Test``) so importing it does not run a no-op suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.notification.entities import Alert, AlertSeverity, Channel
from domain_watcher.core.shared.errors import DeliveryFailedError
from domain_watcher.core.shared.value_objects import DomainName, Duration


def _make_alert() -> Alert:
    """Sample alert used in every test."""

    return Alert(
        domain=DomainName("contract-example.com"),
        threshold=Duration.days(7),
        cycle_id="0123456789abcdef",
        severity=AlertSeverity.WARNING,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )


def _make_channel(notifier_id: str) -> Channel:
    return Channel(id=ChannelId("contract-channel"), notifier_id=notifier_id)


async def _safe_aclose(notifier: object) -> None:
    aclose = getattr(notifier, "aclose", None)
    if aclose is not None:
        await aclose()


class PluginContractTest:
    """Base test class: subclass and implement the two factories.

    The class is named without a leading ``Test`` so pytest does not
    collect it as a standalone test. Subclasses MUST start with
    ``Test`` (the standard pytest convention).

    Override :meth:`make_ok` to return a notifier wired to a transport
    that accepts the call. Override :meth:`make_failing` to return one
    whose transport raises :class:`DeliveryFailedError` on send.
    """

    def make_ok(self) -> Any:
        raise NotImplementedError(
            "Override make_ok() to return a notifier whose transport succeeds."
        )

    def make_failing(self) -> Any:
        raise NotImplementedError(
            "Override make_failing() to return a notifier whose transport fails."
        )

    @pytest.mark.asyncio
    async def test_send_raises_delivery_failed_when_transport_down(self) -> None:
        notifier = self.make_failing()
        try:
            with pytest.raises(DeliveryFailedError):
                await notifier.send(_make_alert(), _make_channel(notifier.id))
        finally:
            await _safe_aclose(notifier)

    @pytest.mark.asyncio
    async def test_send_is_at_least_once_safe(self) -> None:
        """The notifier MUST NOT internally dedup; calling twice must succeed twice."""

        notifier = self.make_ok()
        try:
            await notifier.send(_make_alert(), _make_channel(notifier.id))
            await notifier.send(_make_alert(), _make_channel(notifier.id))
        finally:
            await _safe_aclose(notifier)

    @pytest.mark.asyncio
    async def test_id_is_classvar_string(self) -> None:
        """ADR 0004 §4.2 — ``id`` is a non-empty string classvar."""

        notifier = self.make_ok()
        try:
            assert isinstance(type(notifier).id, str)
            assert type(notifier).id != ""
        finally:
            await _safe_aclose(notifier)

    @pytest.mark.asyncio
    async def test_secrets_not_in_repr(self) -> None:
        """Notifier ``repr`` MUST NOT leak credentials embedded in settings.

        Subclasses can override :meth:`secret_values` to declare which
        substrings must not appear; the default looks at common keys on
        the instance (``bot_token``, ``api_key``, ``password``, ``secret``).
        """

        notifier = self.make_ok()
        try:
            rep = repr(notifier)
            for value in self.secret_values(notifier):
                assert value not in rep, (
                    f"secret value {value!r} appeared in repr({type(notifier).__name__})"
                )
        finally:
            await _safe_aclose(notifier)

    def secret_values(self, notifier: object) -> list[str]:
        """Return the list of secret substrings that MUST NOT appear in ``repr``.

        Default scans known attribute names. Override for custom secrets
        held under non-standard names.
        """

        candidates = (
            "bot_token",
            "api_key",
            "password",
            "secret",
            "token",
            "smtp_password",
            "authorization",
        )
        out: list[str] = []
        for attr in candidates:
            value = getattr(notifier, attr, None)
            if isinstance(value, str) and value:
                out.append(value)
        return out


__all__ = ["PluginContractTest"]
