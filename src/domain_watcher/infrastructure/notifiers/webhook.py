"""Generic HTTP webhook ``Notifier``.

Renders a body via ``string.Template`` (``$var`` / ``${var}``) and POSTs
it to a user-supplied URL with optional headers and HTTP method.

Supported placeholders: ``${domain}``, ``${expires_at}``, ``${threshold}``,
``${severity}``, ``${cycle_id}``. Unknown placeholders cause an eager
``ValueError`` at construction time so a typo is a startup error, not
a silent runtime no-op (per ADR 0003 §3 webhook-template contract).
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

import httpx

from domain_watcher.core.shared.errors import (
    DeliveryFailedError,
    NotificationError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from domain_watcher.core.notification.entities import Alert, Channel


_KNOWN_PLACEHOLDERS: frozenset[str] = frozenset(
    {"domain", "expires_at", "threshold", "severity", "cycle_id"}
)
_ALLOWED_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH"})


def _validate_template(template: string.Template) -> None:
    """Reject templates that reference unknown placeholders."""
    referenced = set(template.get_identifiers())
    unknown = referenced - _KNOWN_PLACEHOLDERS
    if unknown:
        raise ValueError(
            f"WebhookNotifier body_template references unknown placeholders: "
            f"{sorted(unknown)} (allowed: {sorted(_KNOWN_PLACEHOLDERS)})"
        )


def _render(template: string.Template, alert: Alert) -> str:
    return template.substitute(
        domain=alert.domain.value,
        expires_at=alert.expires_at.isoformat(),
        threshold=str(alert.threshold),
        severity=alert.severity.value,
        cycle_id=alert.cycle_id,
    )


@dataclass(slots=True)
class WebhookNotifier:
    """Generic POST-an-alert-to-a-URL adapter."""

    id: ClassVar[str] = "webhook"

    url: str
    body_template: str
    method: str = "POST"
    headers: Mapping[str, str] = field(default_factory=dict)
    content_type: str = "application/json"
    client: httpx.AsyncClient | None = None
    timeout: float = 10.0
    _template: string.Template = field(init=False)
    _owns_client: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("WebhookNotifier.url is required")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("WebhookNotifier.url must be an http(s) URL")
        method = self.method.upper()
        if method not in _ALLOWED_METHODS:
            raise ValueError(
                f"WebhookNotifier.method must be one of {sorted(_ALLOWED_METHODS)}, "
                f"got {self.method!r}"
            )
        # Replace via setattr because slots=True.
        object.__setattr__(self, "method", method)
        template = string.Template(self.body_template)
        _validate_template(template)
        object.__setattr__(self, "_template", template)
        if self.client is None:
            object.__setattr__(self, "client", httpx.AsyncClient(timeout=self.timeout))
            object.__setattr__(self, "_owns_client", True)

    def __repr__(self) -> str:
        # Header values may contain secrets ("Authorization: Bearer ..."): redact them.
        safe_headers = {k: "***" for k in self.headers}
        return (
            f"WebhookNotifier(id={self.id!r}, url={self.url!r}, "
            f"method={self.method!r}, headers={safe_headers!r})"
        )

    async def aclose(self) -> None:
        if self._owns_client and self.client is not None:
            await self.client.aclose()

    async def send(self, alert: Alert, channel: Channel) -> None:
        del channel
        assert self.client is not None
        body = _render(self._template, alert)
        headers = {"Content-Type": self.content_type, **dict(self.headers)}
        try:
            response = await self.client.request(
                self.method, self.url, content=body.encode("utf-8"), headers=headers
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DeliveryFailedError(f"webhook transport failure: {exc}") from exc

        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 429 or 500 <= status < 600:
            raise DeliveryFailedError(f"webhook http {status}")
        if 400 <= status < 500:
            raise NotificationError(f"webhook http {status}")
        raise DeliveryFailedError(f"webhook http {status}")


__all__ = ["WebhookNotifier"]
