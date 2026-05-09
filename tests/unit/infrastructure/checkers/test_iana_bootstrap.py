"""IanaBootstrap: parse, lookup, cache, transport mapping."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from domain_watcher.core.shared.errors import (
    PermanentCheckError,
    TransientCheckError,
)
from domain_watcher.infrastructure.checkers._iana_bootstrap import IanaBootstrap

REGISTRY = {
    "version": 1.0,
    "publication": "2026-01-01T00:00:00Z",
    "services": [
        [["com", "net"], ["https://rdap.verisign.com/com/v1/"]],
        [["ru"], ["https://api.rdap.nic.ru/"]],
        [["app", "dev"], ["https://rdap.googleapis.com/registry"]],
    ],
}


def _client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


async def test_lookup_known_tld() -> None:
    client = _client(lambda req: httpx.Response(200, content=json.dumps(REGISTRY)))
    boot = IanaBootstrap(client=client)
    url = await boot.base_url_for("com")
    assert url == "https://rdap.verisign.com/com/v1"
    await boot.aclose()


async def test_lookup_unknown_tld_permanent() -> None:
    client = _client(lambda req: httpx.Response(200, content=json.dumps(REGISTRY)))
    boot = IanaBootstrap(client=client)
    with pytest.raises(PermanentCheckError):
        await boot.base_url_for("nope")
    await boot.aclose()


async def test_5xx_transient() -> None:
    client = _client(lambda req: httpx.Response(503))
    boot = IanaBootstrap(client=client)
    with pytest.raises(TransientCheckError):
        await boot.base_url_for("com")
    await boot.aclose()


async def test_4xx_permanent() -> None:
    client = _client(lambda req: httpx.Response(404))
    boot = IanaBootstrap(client=client)
    with pytest.raises(PermanentCheckError):
        await boot.base_url_for("com")
    await boot.aclose()


async def test_malformed_json_permanent() -> None:
    client = _client(lambda req: httpx.Response(200, content=b"not json"))
    boot = IanaBootstrap(client=client)
    with pytest.raises(PermanentCheckError):
        await boot.base_url_for("com")
    await boot.aclose()


async def test_cache_hit_avoids_second_request() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=json.dumps(REGISTRY))

    client = _client(handler)
    boot = IanaBootstrap(client=client, ttl=timedelta(hours=1))
    await boot.base_url_for("com")
    await boot.base_url_for("net")
    assert calls["n"] == 1
    await boot.aclose()


_ = (UTC, datetime)
