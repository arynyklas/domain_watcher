"""``WebhookNotifier`` — template rendering, status mapping, header forwarding."""

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
from domain_watcher.infrastructure.notifiers.webhook import WebhookNotifier

URL = "https://hooks.example.test/incoming/abc"


def _alert() -> Alert:
    return Alert(
        domain=DomainName("example.com"),
        expires_at=datetime(2030, 5, 9, 12, 0, 0, tzinfo=UTC),
        threshold=Duration.days(7),
        severity=AlertSeverity.CRITICAL,
        cycle_id="0123456789abcdef",
    )


def _channel() -> Channel:
    return Channel(id=ChannelId("hook"), notifier_id="webhook")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# Template validation --------------------------------------------------------


def test_unknown_placeholder_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="unknown placeholders"):
        WebhookNotifier(
            url=URL,
            body_template='{"x": "${nope}"}',
        )


def test_dollar_dollar_does_not_count_as_placeholder() -> None:
    """``$$`` is a literal ``$`` per ``string.Template``."""
    n = WebhookNotifier(
        url=URL,
        body_template='{"price": "$$5.00", "domain": "${domain}"}',
    )
    assert n is not None  # smoke; ensure construction did not raise


def test_construction_validates_url_and_method() -> None:
    with pytest.raises(ValueError, match="url"):
        WebhookNotifier(url="", body_template="${domain}")
    with pytest.raises(ValueError, match="http"):
        WebhookNotifier(url="ftp://x", body_template="${domain}")
    with pytest.raises(ValueError, match="method"):
        WebhookNotifier(url=URL, body_template="${domain}", method="DELETE")


# Send -----------------------------------------------------------------------


async def test_template_renders_all_placeholders() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["headers"] = dict(req.headers)
        captured["body"] = req.content.decode()
        return httpx.Response(200)

    client = _client(handler)
    body = (
        '{"summary": "${domain} expires at ${expires_at}", '
        '"threshold": "${threshold}", "severity": "${severity}", '
        '"cycle": "${cycle_id}"}'
    )
    n = WebhookNotifier(
        url=URL, body_template=body, client=client, headers={"X-Token": "abc"}
    )
    await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()
    payload = json.loads(captured["body"])
    assert payload["summary"] == "example.com expires at 2030-05-09T12:00:00+00:00"
    assert payload["threshold"] == "7d"
    assert payload["severity"] == "critical"
    assert payload["cycle"] == "0123456789abcdef"
    assert captured["method"] == "POST"
    assert captured["headers"]["x-token"] == "abc"
    assert captured["headers"]["content-type"] == "application/json"


async def test_method_put_supported() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["method"] = req.method
        return httpx.Response(200)

    client = _client(handler)
    n = WebhookNotifier(url=URL, body_template="${domain}", method="PUT", client=client)
    await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()
    assert captured["method"] == "PUT"


async def test_4xx_permanent() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(handler)
    n = WebhookNotifier(url=URL, body_template="${domain}", client=client)
    with pytest.raises(NotificationError):
        await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()


async def test_5xx_retryable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = _client(handler)
    n = WebhookNotifier(url=URL, body_template="${domain}", client=client)
    with pytest.raises(DeliveryFailedError):
        await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()


async def test_429_retryable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = _client(handler)
    n = WebhookNotifier(url=URL, body_template="${domain}", client=client)
    with pytest.raises(DeliveryFailedError):
        await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()


async def test_network_error_retryable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    client = _client(handler)
    n = WebhookNotifier(url=URL, body_template="${domain}", client=client)
    with pytest.raises(DeliveryFailedError):
        await n.send(_alert(), _channel())
    await n.aclose()
    await client.aclose()


def test_repr_redacts_header_values() -> None:
    n = WebhookNotifier(
        url=URL,
        body_template="${domain}",
        headers={"Authorization": "Bearer SUPER-SECRET-TOKEN"},
    )
    rep = repr(n)
    assert "SUPER-SECRET-TOKEN" not in rep
    assert "Authorization" in rep
    assert "***" in rep


def test_id_classvar() -> None:
    assert WebhookNotifier.id == "webhook"
