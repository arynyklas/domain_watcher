"""Telegram ``Notifier`` — direct Bot API HTTP, no aiogram (ADR 0001 §8).

This module is the *standalone* delivery path: a single ``chat_id`` per
configured channel. The bot repository owns multi-recipient delivery
and lives outside this codebase per ADR 0005.

Settings (constructor):

- ``bot_token`` — Telegram Bot API token (kept opaque in repr / logs).
- ``chat_id``    — chat / channel / supergroup id; integer or ``@handle``.
- ``parse_mode`` — ``"HTML"`` (default) or ``"MarkdownV2"``.
- ``api_base``   — override for self-hosted Bot API servers (rare).
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

import httpx

from domain_watcher.core.shared.errors import (
    DeliveryFailedError,
    NotificationError,
)

if TYPE_CHECKING:
    from domain_watcher.core.notification.entities import Alert, Channel


_DEFAULT_API_BASE = "https://api.telegram.org"


def _format_message(alert: Alert, parse_mode: str) -> str:
    """Render the alert body. HTML-escape the dynamic fields."""
    if parse_mode == "HTML":
        return (
            f"<b>Domain expiring</b>\n"
            f"<b>Domain:</b> {html.escape(alert.domain.value)}\n"
            f"<b>Expires:</b> {html.escape(alert.expires_at.isoformat())}\n"
            f"<b>Threshold:</b> {html.escape(str(alert.threshold))}\n"
            f"<b>Severity:</b> {html.escape(alert.severity.value)}"
        )
    # MarkdownV2 path: escape only the characters MarkdownV2 reserves.
    md_specials = r"_*[]()~`>#+-=|{}.!"

    def esc(s: str) -> str:
        return "".join("\\" + c if c in md_specials else c for c in s)

    return (
        f"*Domain expiring*\n"
        f"*Domain:* {esc(alert.domain.value)}\n"
        f"*Expires:* {esc(alert.expires_at.isoformat())}\n"
        f"*Threshold:* {esc(str(alert.threshold))}\n"
        f"*Severity:* {esc(alert.severity.value)}"
    )


@dataclass(slots=True)
class TelegramNotifier:
    """Single-channel Telegram notifier."""

    id: ClassVar[str] = "telegram"

    bot_token: str
    chat_id: str
    parse_mode: str = "HTML"
    api_base: str = _DEFAULT_API_BASE
    client: httpx.AsyncClient | None = None
    timeout: float = 10.0
    _owns_client: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not self.bot_token:
            raise ValueError("TelegramNotifier.bot_token is required")
        if not self.chat_id:
            raise ValueError("TelegramNotifier.chat_id is required")
        if self.parse_mode not in {"HTML", "MarkdownV2"}:
            raise ValueError(
                f"TelegramNotifier.parse_mode must be HTML or MarkdownV2, got {self.parse_mode!r}"
            )
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True

    def __repr__(self) -> str:
        # Never leak the token. ``chat_id`` is operational metadata, fine to log.
        return (
            f"TelegramNotifier(id={self.id!r}, chat_id={self.chat_id!r}, "
            f"parse_mode={self.parse_mode!r})"
        )

    async def aclose(self) -> None:
        if self._owns_client and self.client is not None:
            await self.client.aclose()

    async def send(self, alert: Alert, channel: Channel) -> None:
        del channel  # routing is opaque here; standalone uses constructor chat_id
        assert self.client is not None  # __post_init__ invariant
        url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
        body = {
            "chat_id": self.chat_id,
            "text": _format_message(alert, self.parse_mode),
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            response = await self.client.post(url, json=body)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DeliveryFailedError(f"telegram transport failure: {exc}") from exc

        status = response.status_code
        if 200 <= status < 300:
            return
        # Telegram returns JSON with `description`; fall back gracefully.
        try:
            payload = response.json()
            description = payload.get("description") if isinstance(payload, dict) else None
        except ValueError:
            description = None
        msg = f"telegram http {status}"
        if description:
            msg = f"{msg}: {description}"

        if status == 401:
            raise NotificationError(f"{msg} (invalid bot token)")
        if status == 403:
            # Bot was kicked or chat_id wrong — operator must fix; don't loop.
            raise NotificationError(msg)
        if status == 429 or 500 <= status < 600:
            raise DeliveryFailedError(msg)
        if 400 <= status < 500:
            # 400 with a Telegram-specific reason is permanent (bad chat_id, parse error).
            raise NotificationError(msg)
        raise DeliveryFailedError(msg)


__all__ = ["TelegramNotifier"]
