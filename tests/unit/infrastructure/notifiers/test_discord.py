"""``DiscordNotifier`` — webhook payload + status mapping."""

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
from domain_watcher.infrastructure.notifiers.discord import DiscordNotifier

WEBHOOK = "https://discord.test/api/webhooks/123/abcDEF"


def _alert(severity: AlertSeverity = AlertSeverity.WARNING) -> Alert:
    return Alert(
        domain=DomainName("example.com"),
        expires_at=datetime(2030, 5, 9, tzinfo=UTC),
        threshold=Duration.days(7),
        severity=severity,
        cycle_id="0123456789abcdef",
    )


def _channel() -> Channel:
    return Channel(id=ChannelId("discord-eng"), notifier_id="discord")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_happy_path_posts_embed() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content.decode())
        return httpx.Response(204)

    client = _client(handler)
    n = DiscordNotifier(webhook_url=WEBHOOK, client=client)
    await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()
    assert captured["url"] == WEBHOOK
    body = captured["body"]
    assert "example.com" in body["content"]
    assert len(body["embeds"]) == 1
    embed = body["embeds"][0]
    assert "Domain expiring" in embed["title"]
    assert "2030-05-09" in embed["description"]
    assert embed["color"] == 0xE67E22  # amber for WARNING


async def test_severity_critical_sets_red_color() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content.decode())
        return httpx.Response(204)

    client = _client(handler)
    n = DiscordNotifier(webhook_url=WEBHOOK, client=client)
    await n.send(_alert(severity=AlertSeverity.CRITICAL), _channel())
    await n.aclose()
    await client.aclose()
    assert captured["body"]["embeds"][0]["color"] == 0xE74C3C


async def test_username_and_avatar_forwarded() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content.decode())
        return httpx.Response(204)

    client = _client(handler)
    n = DiscordNotifier(
        webhook_url=WEBHOOK,
        username="DomainBot",
        avatar_url="https://example.test/icon.png",
        client=client,
    )
    await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()
    assert captured["body"]["username"] == "DomainBot"
    assert captured["body"]["avatar_url"] == "https://example.test/icon.png"


async def test_429_retryable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = _client(handler)
    n = DiscordNotifier(webhook_url=WEBHOOK, client=client)
    with pytest.raises(DeliveryFailedError):
        await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()


async def test_404_permanent() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(handler)
    n = DiscordNotifier(webhook_url=WEBHOOK, client=client)
    with pytest.raises(NotificationError, match="webhook invalid"):
        await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()


async def test_5xx_retryable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = _client(handler)
    n = DiscordNotifier(webhook_url=WEBHOOK, client=client)
    with pytest.raises(DeliveryFailedError):
        await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()


async def test_network_failure_raises_delivery_failed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client(handler)
    n = DiscordNotifier(webhook_url=WEBHOOK, client=client)
    with pytest.raises(DeliveryFailedError):
        await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()


def test_construction_validates_url() -> None:
    with pytest.raises(ValueError, match="webhook_url"):
        DiscordNotifier(webhook_url="")
    with pytest.raises(ValueError, match="http"):
        DiscordNotifier(webhook_url="ftp://nope")


def test_repr_does_not_leak_url() -> None:
    n = DiscordNotifier(webhook_url=WEBHOOK + "/SECRET-TOKEN")
    assert "SECRET-TOKEN" not in repr(n)


def test_id_classvar() -> None:
    assert DiscordNotifier.id == "discord"
