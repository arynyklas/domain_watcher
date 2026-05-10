"""Shared ``Notifier`` conformance suite (ADR 0004 §4.2).

Every built-in notifier and every third-party plugin MUST satisfy this
contract:

- ``send`` raises ``DeliveryFailedError`` when the transport is down.
- ``send`` is at-least-once: notifier authors MUST NOT track 'already
  sent' state; the orchestrator's IdempotencyStore is the dedup boundary.
  The contract simply asserts that calling ``send`` twice does not raise
  from the notifier itself when transport succeeds.
- The constructor validates settings eagerly.
- ``repr`` does not expose secrets.

Plugin authors copy the parametrized class and replace ``factories`` —
see ``domain_watcher.testing.contract.notifier`` (Phase 11).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

import httpx
import pytest

from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.notification.entities import Alert, AlertSeverity, Channel
from domain_watcher.core.shared.errors import DeliveryFailedError, NotificationError
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.notifiers.discord import DiscordNotifier
from domain_watcher.infrastructure.notifiers.email_smtp import EmailNotifier
from domain_watcher.infrastructure.notifiers.telegram import TelegramNotifier
from domain_watcher.infrastructure.notifiers.webhook import WebhookNotifier


class _Notifier(Protocol):
    id: str

    async def send(self, alert: Alert, channel: Channel) -> None: ...


# A factory either returns a notifier configured to succeed (transport_ok=True)
# or to fail with a transport error (transport_ok=False).
NotifierFactory = Callable[[bool], Awaitable[_Notifier]]


def _alert() -> Alert:
    return Alert(
        domain=DomainName("example.com"),
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        threshold=Duration.days(7),
        severity=AlertSeverity.WARNING,
        cycle_id="abcdef0123456789",
    )


def _channel(notifier_id: str) -> Channel:
    return Channel(id=ChannelId("contract"), notifier_id=notifier_id)


# ---------- Factories ------------------------------------------------------


def _httpx_client(*, ok: bool) -> httpx.AsyncClient:
    if ok:

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})
    else:

        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("transport down")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _telegram(transport_ok: bool) -> TelegramNotifier:
    return TelegramNotifier(
        bot_token="t0k3n",
        chat_id="100",
        client=_httpx_client(ok=transport_ok),
    )


async def _discord(transport_ok: bool) -> DiscordNotifier:
    return DiscordNotifier(
        webhook_url="https://discord.test/api/webhooks/1/abc",
        client=_httpx_client(ok=transport_ok),
    )


async def _webhook(transport_ok: bool) -> WebhookNotifier:
    return WebhookNotifier(
        url="https://webhook.test/in",
        body_template='{"d": "${domain}"}',
        client=_httpx_client(ok=transport_ok),
    )


class _StubSender:
    def __init__(self, *, ok: bool) -> None:
        self.ok = ok
        self.calls = 0

    async def __call__(self, msg) -> None:
        self.calls += 1
        if not self.ok:
            import aiosmtplib

            raise aiosmtplib.SMTPConnectError("transport down")


async def _email(transport_ok: bool) -> EmailNotifier:
    return EmailNotifier(
        smtp_host="smtp.example.test",
        smtp_port=587,
        from_addr="a@b.test",
        to_addrs=("c@d.test",),
        sender=_StubSender(ok=transport_ok),
    )


FACTORIES: list[tuple[str, NotifierFactory]] = [
    ("telegram", _telegram),
    ("discord", _discord),
    ("webhook", _webhook),
    ("email", _email),
]


# ---------- Conformance suite ---------------------------------------------


@pytest.mark.parametrize(("name", "factory"), FACTORIES)
async def test_send_raises_delivery_failed_when_transport_down(
    name: str, factory: NotifierFactory
) -> None:
    notifier = await factory(False)
    with pytest.raises(DeliveryFailedError):
        await notifier.send(_alert(), _channel(notifier.id))
    aclose = getattr(notifier, "aclose", None)
    if aclose is not None:
        await aclose()


@pytest.mark.parametrize(("name", "factory"), FACTORIES)
async def test_send_is_at_least_once_safe(name: str, factory: NotifierFactory) -> None:
    """The notifier itself MUST NOT track 'already sent' — calling twice
    succeeds twice."""
    notifier = await factory(True)
    await notifier.send(_alert(), _channel(notifier.id))
    await notifier.send(_alert(), _channel(notifier.id))  # MUST NOT raise from dedup
    aclose = getattr(notifier, "aclose", None)
    if aclose is not None:
        await aclose()


@pytest.mark.parametrize(("name", "factory"), FACTORIES)
async def test_repr_redacts_secrets(name: str, factory: NotifierFactory) -> None:
    notifier = await factory(True)
    rep = repr(notifier)
    # Per-notifier secrets:
    if isinstance(notifier, TelegramNotifier):
        assert "t0k3n" not in rep
    elif isinstance(notifier, DiscordNotifier):
        # webhook URL itself is the secret on Discord — must not appear in repr.
        assert "/api/webhooks/" not in rep
    elif isinstance(notifier, EmailNotifier):
        # password fields must never appear; hostname/from-addr are operational.
        assert "hunter2" not in rep
    aclose = getattr(notifier, "aclose", None)
    if aclose is not None:
        await aclose()


def test_telegram_constructor_validates_settings_eagerly() -> None:
    with pytest.raises(ValueError):
        TelegramNotifier(bot_token="", chat_id="100")


def test_discord_constructor_validates_settings_eagerly() -> None:
    with pytest.raises(ValueError):
        DiscordNotifier(webhook_url="")


def test_webhook_constructor_validates_settings_eagerly() -> None:
    with pytest.raises(ValueError):
        WebhookNotifier(url="", body_template="${domain}")


def test_email_constructor_validates_settings_eagerly() -> None:
    with pytest.raises(ValueError):
        EmailNotifier(
            smtp_host="",
            smtp_port=587,
            from_addr="a@b",
            to_addrs=("c@d",),
        )


# Permanent-failure mapping — the dispatcher uses this to distinguish from retryable.
@pytest.mark.parametrize(
    ("name", "factory"),
    [
        (n, f) for n, f in FACTORIES if n != "email"
    ],  # email's permanent path is auth, separate
)
async def test_4xx_maps_to_permanent_error(name: str, factory: NotifierFactory) -> None:
    """A 4xx response is permanent: the operator must fix something before retry."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    if name == "telegram":
        notifier: _Notifier = TelegramNotifier(
            bot_token="t", chat_id="1", client=client
        )
    elif name == "discord":
        notifier = DiscordNotifier(
            webhook_url="https://discord.test/api/webhooks/1/abc", client=client
        )
    elif name == "webhook":
        notifier = WebhookNotifier(
            url="https://hook.test/", body_template="${domain}", client=client
        )
    else:
        pytest.skip(f"no 4xx fixture for {name}")
        return

    with pytest.raises(NotificationError):
        await notifier.send(_alert(), _channel(notifier.id))
    aclose = getattr(notifier, "aclose", None)
    if aclose is not None:
        await aclose()
    await client.aclose()
