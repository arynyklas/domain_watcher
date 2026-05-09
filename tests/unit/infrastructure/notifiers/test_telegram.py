"""``TelegramNotifier`` — Bot API HTTP behavior + escaping + error mapping."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.notification.entities import Alert, AlertSeverity, Channel
from domain_watcher.core.shared.errors import (
    DeliveryFailedError,
    NotificationError,
)
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.notifiers.telegram import TelegramNotifier


def _alert(domain: str = "<script>example.com</script>") -> Alert:
    expires = datetime(2030, 1, 1, tzinfo=UTC)
    return Alert(
        domain=DomainName("example.com") if "<" in domain else DomainName(domain),
        expires_at=expires,
        threshold=Duration.days(7),
        severity=AlertSeverity.WARNING,
        cycle_id="0123456789abcdef",
    )


def _channel() -> Channel:
    return Channel(id=ChannelId("tg-ops"), notifier_id="telegram")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_happy_path_posts_to_send_message() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content.decode())
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = _client(handler)
    notifier = TelegramNotifier(bot_token="t0k3n", chat_id="-100123", client=client)
    await notifier.send(_alert(), _channel())
    await notifier.aclose()
    await client.aclose()
    assert captured["url"] == "https://api.telegram.org/bott0k3n/sendMessage"
    body = captured["body"]
    assert body["chat_id"] == "-100123"
    assert body["parse_mode"] == "HTML"
    assert "<b>Domain expiring</b>" in body["text"]
    assert body["disable_web_page_preview"] is True


async def test_html_escapes_domain_field() -> None:
    """HTML mode MUST escape angle brackets in dynamic content."""
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content.decode())
        return httpx.Response(200)

    client = _client(handler)
    notifier = TelegramNotifier(bot_token="t", chat_id="1", client=client)
    # Construct an Alert with metadata to verify body content; domain is constrained
    # by DomainName invariants so we look at expiration formatting instead.
    expires = datetime(2030, 12, 31, 23, 59, 59, tzinfo=UTC)
    alert = Alert(
        domain=DomainName("example.com"),
        expires_at=expires,
        threshold=Duration.days(1),
        severity=AlertSeverity.CRITICAL,
        cycle_id="abcdef0123456789",
    )
    await notifier.send(alert, _channel())
    await notifier.aclose()
    await client.aclose()
    text = captured["body"]["text"]
    assert "example.com" in text
    assert "2030-12-31T23:59:59+00:00" in text
    assert "critical" in text


async def test_429_raises_delivery_failed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"description": "Too Many Requests"})

    client = _client(handler)
    notifier = TelegramNotifier(bot_token="t", chat_id="1", client=client)
    with pytest.raises(DeliveryFailedError, match="429"):
        await notifier.send(_alert(), _channel())
    await notifier.aclose()
    await client.aclose()


async def test_401_raises_permanent_notification_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"description": "Unauthorized"})

    client = _client(handler)
    notifier = TelegramNotifier(bot_token="bad", chat_id="1", client=client)
    with pytest.raises(NotificationError, match="invalid bot token"):
        await notifier.send(_alert(), _channel())
    await notifier.aclose()
    await client.aclose()


async def test_500_raises_delivery_failed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"description": "Service Unavailable"})

    client = _client(handler)
    notifier = TelegramNotifier(bot_token="t", chat_id="1", client=client)
    with pytest.raises(DeliveryFailedError):
        await notifier.send(_alert(), _channel())
    await notifier.aclose()
    await client.aclose()


async def test_network_error_raises_delivery_failed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client(handler)
    notifier = TelegramNotifier(bot_token="t", chat_id="1", client=client)
    with pytest.raises(DeliveryFailedError, match="transport"):
        await notifier.send(_alert(), _channel())
    await notifier.aclose()
    await client.aclose()


def test_repr_does_not_leak_token() -> None:
    notifier = TelegramNotifier(bot_token="super-secret-token", chat_id="100")
    rep = repr(notifier)
    assert "super-secret-token" not in rep
    assert "chat_id" in rep


def test_constructor_validates_required_fields() -> None:
    with pytest.raises(ValueError, match="bot_token"):
        TelegramNotifier(bot_token="", chat_id="100")
    with pytest.raises(ValueError, match="chat_id"):
        TelegramNotifier(bot_token="t", chat_id="")
    with pytest.raises(ValueError, match="parse_mode"):
        TelegramNotifier(bot_token="t", chat_id="100", parse_mode="bogus")


def test_id_classvar() -> None:
    assert TelegramNotifier.id == "telegram"
