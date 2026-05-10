"""Discord webhook ``Notifier``.

Posts an embed to a Discord webhook URL. Embed colour is derived from
``Alert.severity``: INFO blue, WARNING amber, CRITICAL red.

Settings:

- ``webhook_url`` — full Discord webhook URL (kept opaque in repr).
- ``username``    — optional override for the webhook's display name.
- ``avatar_url``  — optional avatar override.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, ClassVar

import httpx

from domain_watcher.core.notification.entities import AlertSeverity
from domain_watcher.core.shared.errors import (
    DeliveryFailedError,
    NotificationError,
)
from domain_watcher.infrastructure._http import (
    HTTP_2XX_MAX,
    HTTP_2XX_MIN,
    HTTP_4XX_MIN,
    HTTP_5XX_MAX,
    HTTP_5XX_MIN,
)

if TYPE_CHECKING:
    from domain_watcher.core.notification.entities import Alert, Channel


_SEVERITY_COLOR: dict[AlertSeverity, int] = {
    AlertSeverity.INFO: 0x3498DB,  # blue
    AlertSeverity.WARNING: 0xE67E22,  # amber
    AlertSeverity.CRITICAL: 0xE74C3C,  # red
}


def _build_payload(
    alert: Alert, *, username: str | None, avatar_url: str | None
) -> dict:
    embed = {
        "title": f"Domain expiring: {alert.domain.value}",
        "description": (
            f"**Expires:** {alert.expires_at.isoformat()}\n"
            f"**Threshold:** {alert.threshold}\n"
            f"**Severity:** {alert.severity.value}\n"
            f"**Cycle id:** `{alert.cycle_id}`"
        ),
        "color": _SEVERITY_COLOR.get(alert.severity, 0x95A5A6),
    }
    body: dict = {
        "content": f"`{alert.domain.value}` expires at {alert.expires_at.isoformat()}",
        "embeds": [embed],
    }
    if username is not None:
        body["username"] = username
    if avatar_url is not None:
        body["avatar_url"] = avatar_url
    return body


@dataclass(slots=True)
class DiscordNotifier:
    """Discord webhook ``Notifier``."""

    id: ClassVar[str] = "discord"

    webhook_url: str
    username: str | None = None
    avatar_url: str | None = None
    client: httpx.AsyncClient | None = None
    timeout: float = 10.0
    _owns_client: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not self.webhook_url:
            raise ValueError("DiscordNotifier.webhook_url is required")
        if not self.webhook_url.startswith(("http://", "https://")):
            raise ValueError("DiscordNotifier.webhook_url must be an http(s) URL")
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True

    def __repr__(self) -> str:
        return f"DiscordNotifier(id={self.id!r}, username={self.username!r})"

    async def aclose(self) -> None:
        if self._owns_client and self.client is not None:
            await self.client.aclose()

    async def send(self, alert: Alert, channel: Channel) -> None:
        del channel
        if self.client is None:
            raise RuntimeError(
                "DiscordNotifier.client is None — __post_init__ invariant violated"
            )
        body = _build_payload(alert, username=self.username, avatar_url=self.avatar_url)
        try:
            response = await self.client.post(self.webhook_url, json=body)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DeliveryFailedError(f"discord transport failure: {exc}") from exc

        status = response.status_code
        if HTTP_2XX_MIN <= status < HTTP_2XX_MAX:
            return
        if status in {
            HTTPStatus.UNAUTHORIZED,
            HTTPStatus.FORBIDDEN,
            HTTPStatus.NOT_FOUND,
        }:
            # 404 = webhook deleted; not retryable.
            raise NotificationError(
                f"discord http {status}: webhook invalid or revoked"
            )
        if (
            status == HTTPStatus.TOO_MANY_REQUESTS
            or HTTP_5XX_MIN <= status < HTTP_5XX_MAX
        ):
            raise DeliveryFailedError(f"discord http {status}")
        if HTTP_4XX_MIN <= status < HTTP_5XX_MIN:
            raise NotificationError(f"discord http {status}")
        raise DeliveryFailedError(f"discord http {status}")


__all__ = ["DiscordNotifier"]
