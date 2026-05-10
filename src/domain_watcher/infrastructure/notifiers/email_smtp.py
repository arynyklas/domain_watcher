"""SMTP ``Notifier`` — multipart text+html via ``aiosmtplib``.

Settings (constructor):

- ``smtp_host``    — host name of the SMTP relay.
- ``smtp_port``    — TCP port (587 STARTTLS, 465 implicit TLS, 25 plain).
- ``username``     — SASL username (None ⇒ unauthenticated).
- ``password``     — SASL password.
- ``from_addr``    — RFC 5322 ``From`` header.
- ``to_addrs``     — list of recipient addresses (envelope + header).
- ``use_starttls`` — STARTTLS upgrade after EHLO. Default True.
- ``use_tls``      — implicit TLS (port 465). Mutually exclusive with STARTTLS.
- ``allow_insecure`` — required to send plain SMTP (no TLS at all).

Permanent failures (``SMTPAuthenticationError``, bad address, …) raise
``NotificationError``. Transient failures (timeouts, connection refused,
4xx/5xx response codes that aren't auth) raise ``DeliveryFailedError``
so the dispatch loop's retry policy can decide what to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import TYPE_CHECKING, ClassVar

import aiosmtplib

from domain_watcher.core.shared.errors import (
    DeliveryFailedError,
    NotificationError,
)
from domain_watcher.infrastructure._http import HTTP_4XX_MIN, HTTP_5XX_MIN

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from domain_watcher.core.notification.entities import Alert, Channel


def _build_message(
    *, alert: Alert, from_addr: str, to_addrs: Sequence[str]
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = (
        f"[{alert.severity.value.upper()}] {alert.domain.value} "
        f"expires in {alert.threshold}"
    )
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    text_body = (
        f"Domain: {alert.domain.value}\n"
        f"Expires at: {alert.expires_at.isoformat()}\n"
        f"Threshold:  {alert.threshold}\n"
        f"Severity:   {alert.severity.value}\n"
        f"Cycle id:   {alert.cycle_id}\n"
    )
    msg.set_content(text_body)
    html_body = (
        "<html><body>"
        "<h2>Domain expiring</h2>"
        f"<p><b>Domain:</b> {alert.domain.value}</p>"
        f"<p><b>Expires at:</b> {alert.expires_at.isoformat()}</p>"
        f"<p><b>Threshold:</b> {alert.threshold}</p>"
        f"<p><b>Severity:</b> {alert.severity.value}</p>"
        f"<p><b>Cycle id:</b> <code>{alert.cycle_id}</code></p>"
        "</body></html>"
    )
    msg.add_alternative(html_body, subtype="html")
    return msg


@dataclass(slots=True)
class EmailNotifier:
    """SMTP-backed ``Notifier``."""

    id: ClassVar[str] = "email"

    smtp_host: str
    smtp_port: int
    from_addr: str
    to_addrs: tuple[str, ...]
    username: str | None = None
    password: str | None = None
    use_starttls: bool = True
    use_tls: bool = False
    allow_insecure: bool = False
    timeout: float = 30.0
    # Inject for tests; production code instantiates aiosmtplib.SMTP per call
    # to keep the connection lifetime short.
    sender: Callable[[EmailMessage], Awaitable[None]] | None = field(default=None)

    def __post_init__(self) -> None:
        if not self.smtp_host:
            raise ValueError("EmailNotifier.smtp_host is required")
        if self.smtp_port <= 0:
            raise ValueError(
                f"EmailNotifier.smtp_port must be > 0, got {self.smtp_port}"
            )
        if not self.from_addr:
            raise ValueError("EmailNotifier.from_addr is required")
        if not self.to_addrs:
            raise ValueError("EmailNotifier.to_addrs must be non-empty")
        if self.use_starttls and self.use_tls:
            raise ValueError(
                "EmailNotifier: use_starttls and use_tls are mutually exclusive"
            )
        if not self.use_starttls and not self.use_tls and not self.allow_insecure:
            raise ValueError(
                "EmailNotifier: refusing plain SMTP unless allow_insecure=True"
            )

    def __repr__(self) -> str:
        return (
            f"EmailNotifier(id={self.id!r}, smtp_host={self.smtp_host!r}, "
            f"smtp_port={self.smtp_port}, from_addr={self.from_addr!r}, "
            f"to_addrs={self.to_addrs!r})"
        )

    async def send(self, alert: Alert, channel: Channel) -> None:
        del channel
        msg = _build_message(
            alert=alert, from_addr=self.from_addr, to_addrs=self.to_addrs
        )
        try:
            await self._dispatch(msg)
        except aiosmtplib.SMTPAuthenticationError as exc:
            raise NotificationError(f"smtp auth failure: {exc}") from exc
        except (
            aiosmtplib.SMTPConnectError,
            aiosmtplib.SMTPConnectTimeoutError,
            aiosmtplib.SMTPReadTimeoutError,
            aiosmtplib.SMTPTimeoutError,
        ) as exc:
            raise DeliveryFailedError(f"smtp transport failure: {exc}") from exc
        except aiosmtplib.SMTPResponseException as exc:
            # 4xx → transient, 5xx → permanent. aiosmtplib uses .code.
            code = getattr(exc, "code", 500)
            if HTTP_4XX_MIN <= code < HTTP_5XX_MIN:
                raise DeliveryFailedError(f"smtp transient {code}: {exc}") from exc
            raise NotificationError(f"smtp permanent {code}: {exc}") from exc
        except aiosmtplib.SMTPException as exc:
            # Catch-all; treat as transient and let retry decide.
            raise DeliveryFailedError(f"smtp error: {exc}") from exc

    async def _dispatch(self, msg: EmailMessage) -> None:
        if self.sender is not None:
            # Tests inject a callable that consumes the message.
            await self.sender(msg)
            return
        await aiosmtplib.send(
            msg,
            hostname=self.smtp_host,
            port=self.smtp_port,
            username=self.username,
            password=self.password,
            use_tls=self.use_tls,
            start_tls=self.use_starttls,
            timeout=self.timeout,
        )


__all__ = ["EmailNotifier"]
