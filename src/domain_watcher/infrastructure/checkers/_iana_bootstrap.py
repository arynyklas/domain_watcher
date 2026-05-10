"""IANA RDAP bootstrap (``https://data.iana.org/rdap/dns.json``).

Resolves a TLD to the RDAP base URL its registry publishes. Cached for
24h in-process; a process restart costs at most one extra fetch.

The bootstrap port lives behind a ``BootstrapResolver`` Protocol so unit
tests inject a fake without hitting the network. The default impl uses
``httpx.AsyncClient``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import httpx

from domain_watcher.core.shared.errors import (
    PermanentCheckError,
    TransientCheckError,
)
from domain_watcher.infrastructure._http import HTTP_4XX_MIN, HTTP_5XX_MIN

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_URL = "https://data.iana.org/rdap/dns.json"
DEFAULT_TTL = timedelta(hours=24)

# IANA bootstrap services entries are ``[[tlds...], [urls...]]`` — at least
# two list elements are required for a usable record.
_MIN_SERVICE_ENTRY_LEN = 2


class BootstrapResolver(Protocol):
    """Resolves ``tld → base_url``."""

    async def base_url_for(self, tld: str) -> str: ...


@dataclass(frozen=True, slots=True)
class _CachedRegistry:
    services: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]
    fetched_at: datetime

    def lookup(self, tld: str) -> str | None:
        target = tld.lower()
        for tlds, urls in self.services:
            if target in tlds and urls:
                # Prefer https if present
                for url in urls:
                    if url.startswith("https://"):
                        return url
                return urls[0]
        return None


class IanaBootstrap:
    """Default bootstrap resolver hitting ``data.iana.org``.

    Tests inject ``http_get`` to short-circuit network calls; production
    wires a real ``httpx.AsyncClient.get``.
    """

    __slots__ = ("_cache", "_client", "_owns_client", "_ttl", "_url")

    def __init__(
        self,
        *,
        url: str = DEFAULT_URL,
        ttl: timedelta = DEFAULT_TTL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._ttl = ttl
        self._cache: _CachedRegistry | None = None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def base_url_for(self, tld: str) -> str:
        registry = await self._get_registry()
        url = registry.lookup(tld)
        if url is None:
            raise PermanentCheckError(f"no RDAP service for tld {tld!r}")
        return url.rstrip("/")

    async def _get_registry(self) -> _CachedRegistry:
        now = datetime.now(tz=UTC)
        if self._cache is not None and (now - self._cache.fetched_at) < self._ttl:
            return self._cache
        try:
            response = await self._client.get(self._url)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientCheckError(f"IANA bootstrap fetch failed: {exc}") from exc
        if response.status_code >= HTTP_5XX_MIN:
            raise TransientCheckError(f"IANA bootstrap http {response.status_code}")
        if response.status_code >= HTTP_4XX_MIN:
            raise PermanentCheckError(f"IANA bootstrap http {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise PermanentCheckError(f"IANA bootstrap malformed JSON: {exc}") from exc
        self._cache = _parse_registry(data, fetched_at=now)
        return self._cache


def _parse_registry(
    data: Mapping[str, object], *, fetched_at: datetime
) -> _CachedRegistry:
    services_raw = data.get("services")
    if not isinstance(services_raw, list):
        raise PermanentCheckError("IANA bootstrap missing 'services' array")
    services: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for entry in services_raw:
        if not isinstance(entry, list) or len(entry) < _MIN_SERVICE_ENTRY_LEN:
            continue
        tlds_part, urls_part = entry[0], entry[1]
        if not isinstance(tlds_part, list) or not isinstance(urls_part, list):
            continue
        tlds = tuple(str(t).lower() for t in tlds_part)
        urls = tuple(str(u) for u in urls_part)
        services.append((tlds, urls))
    return _CachedRegistry(services=tuple(services), fetched_at=fetched_at)


__all__ = ["BootstrapResolver", "IanaBootstrap"]
