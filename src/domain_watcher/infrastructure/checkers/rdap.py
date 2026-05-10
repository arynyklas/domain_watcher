"""RDAP-based ``ExpirationChecker`` (id ``"rdap"``).

Resolves the registry RDAP endpoint via ``IanaBootstrap``, fetches
``/domain/<fqdn>``, and extracts the ``expiration`` event date.

Maps HTTP status / network failures to the canonical
``CheckResult``/``CheckOutcome`` pair so the use case can decide whether
to retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, ClassVar, cast

import httpx

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.shared.errors import (
    PermanentCheckError,
    TransientCheckError,
)
from domain_watcher.infrastructure._http import (
    HTTP_4XX_MIN,
    HTTP_5XX_MAX,
    HTTP_5XX_MIN,
)

if TYPE_CHECKING:
    from domain_watcher.core.shared.value_objects import DomainName
    from domain_watcher.infrastructure.checkers._iana_bootstrap import BootstrapResolver


def _parse_expiration(payload: dict[str, object]) -> datetime | None:
    events = payload.get("events")
    if not isinstance(events, list):
        return None
    for raw_ev in events:
        if not isinstance(raw_ev, dict):
            continue
        ev = cast("dict[str, object]", raw_ev)
        if ev.get("eventAction") != "expiration":
            continue
        date_str = ev.get("eventDate")
        if not isinstance(date_str, str):
            continue
        return _parse_iso(date_str)
    return None


def _parse_iso(value: str) -> datetime:
    # RDAP requires RFC 3339 / ISO-8601 with offset; accept "Z".
    raw = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(slots=True)
class RdapChecker:
    """``ExpirationChecker`` backed by the registry RDAP endpoint."""

    id: ClassVar[str] = "rdap"

    bootstrap: BootstrapResolver
    client: httpx.AsyncClient

    async def check(self, domain: DomainName) -> CheckResult:  # noqa: PLR0911
        try:
            base = await self.bootstrap.base_url_for(domain.tld)
        except TransientCheckError as exc:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.TRANSIENT_ERROR,
                expires_at=None,
                source=self.id,
                error=str(exc),
            )
        except PermanentCheckError as exc:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                error=str(exc),
            )

        url = f"{base}/domain/{domain.value}"
        try:
            response = await self.client.get(url)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.TRANSIENT_ERROR,
                expires_at=None,
                source=self.id,
                error=f"{type(exc).__name__}: {exc}",
            )

        if response.status_code == HTTPStatus.NOT_FOUND:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                error="rdap 404 (no such domain)",
            )
        if HTTP_5XX_MIN <= response.status_code < HTTP_5XX_MAX:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.TRANSIENT_ERROR,
                expires_at=None,
                source=self.id,
                error=f"rdap http {response.status_code}",
            )
        if HTTP_4XX_MIN <= response.status_code < HTTP_5XX_MIN:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                error=f"rdap http {response.status_code}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                error=f"malformed rdap json: {exc}",
            )
        if not isinstance(payload, dict):
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                error="rdap payload not an object",
            )
        expires_at = _parse_expiration(payload)
        if expires_at is None:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                error="no expiration event in rdap payload",
            )
        return CheckResult(
            domain=domain,
            outcome=CheckOutcome.OK,
            expires_at=expires_at,
            source=self.id,
        )


__all__ = ["RdapChecker"]
