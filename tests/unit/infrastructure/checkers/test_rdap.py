"""RdapChecker: bootstrap miss, 404, 5xx, timeout, malformed JSON, happy path."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest

from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.shared.errors import (
    PermanentCheckError,
    TransientCheckError,
)
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.checkers.rdap import RdapChecker


@dataclass
class FakeBootstrap:
    base_url: str | None = "https://rdap.test/com"
    raises: BaseException | None = None

    async def base_url_for(self, tld: str) -> str:
        if self.raises is not None:
            raise self.raises
        if self.base_url is None:
            raise PermanentCheckError(f"no rdap for {tld}")
        return self.base_url


def _client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


async def test_happy_path_returns_ok() -> None:
    payload = {
        "events": [
            {"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
            {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"},
        ]
    }

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/domain/example.com")
        return httpx.Response(200, content=json.dumps(payload))

    client = _client(handler)
    checker = RdapChecker(bootstrap=FakeBootstrap(), client=client)
    result = await checker.check(DomainName("example.com"))
    await client.aclose()
    assert result.outcome is CheckOutcome.OK
    assert result.expires_at == datetime(2030, 1, 1, tzinfo=UTC)


async def test_404_permanent() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(handler)
    checker = RdapChecker(bootstrap=FakeBootstrap(), client=client)
    result = await checker.check(DomainName("example.com"))
    await client.aclose()
    assert result.outcome is CheckOutcome.PERMANENT_ERROR


async def test_500_transient() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = _client(handler)
    checker = RdapChecker(bootstrap=FakeBootstrap(), client=client)
    result = await checker.check(DomainName("example.com"))
    await client.aclose()
    assert result.outcome is CheckOutcome.TRANSIENT_ERROR


async def test_connection_reset_transient() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("reset")

    client = _client(handler)
    checker = RdapChecker(bootstrap=FakeBootstrap(), client=client)
    result = await checker.check(DomainName("example.com"))
    await client.aclose()
    assert result.outcome is CheckOutcome.TRANSIENT_ERROR


async def test_malformed_json_permanent() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _client(handler)
    checker = RdapChecker(bootstrap=FakeBootstrap(), client=client)
    result = await checker.check(DomainName("example.com"))
    await client.aclose()
    assert result.outcome is CheckOutcome.PERMANENT_ERROR


async def test_no_expiration_event_permanent() -> None:
    payload = {
        "events": [{"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"}]
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload))

    client = _client(handler)
    checker = RdapChecker(bootstrap=FakeBootstrap(), client=client)
    result = await checker.check(DomainName("example.com"))
    await client.aclose()
    assert result.outcome is CheckOutcome.PERMANENT_ERROR
    assert "no expiration" in (result.error or "")


async def test_bootstrap_unknown_tld_permanent() -> None:
    def handler(
        req: httpx.Request,
    ) -> httpx.Response:  # pragma: no cover — never called
        return httpx.Response(200)

    client = _client(handler)
    checker = RdapChecker(
        bootstrap=FakeBootstrap(base_url=None),
        client=client,
    )
    result = await checker.check(DomainName("example.zzunknown"))
    await client.aclose()
    assert result.outcome is CheckOutcome.PERMANENT_ERROR


async def test_bootstrap_transient_propagates() -> None:
    def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200)

    client = _client(handler)
    checker = RdapChecker(
        bootstrap=FakeBootstrap(raises=TransientCheckError("flake")),
        client=client,
    )
    result = await checker.check(DomainName("example.com"))
    await client.aclose()
    assert result.outcome is CheckOutcome.TRANSIENT_ERROR


_ = pytest  # silence unused import when no markers are applied
