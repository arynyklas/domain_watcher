"""``EmailNotifier`` — message construction + transport error mapping.

The module-under-test injects a sender for unit testing; the real
``aiosmtplib`` round-trip is exercised in the integration test
``tests/integration/notifiers/test_email_smtp_real.py`` against
``mailpit`` (Phase 6 task 6.2 step 3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosmtplib
import pytest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from email.message import EmailMessage

from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.notification.entities import Alert, AlertSeverity, Channel
from domain_watcher.core.shared.errors import (
    DeliveryFailedError,
    NotificationError,
)
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.notifiers.email_smtp import EmailNotifier


def _alert() -> Alert:
    return Alert(
        domain=DomainName("example.com"),
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        threshold=Duration.days(7),
        severity=AlertSeverity.CRITICAL,
        cycle_id="abcdef0123456789",
    )


def _channel() -> Channel:
    return Channel(id=ChannelId("email-team"), notifier_id="email")


def _notifier(
    *,
    sender: Callable[[EmailMessage], Awaitable[None]] | None,
    smtp_host: str = "smtp.example.test",
    smtp_port: int = 587,
    from_addr: str = "alerts@example.test",
    to_addrs: tuple[str, ...] = ("ops@example.test",),
    username: str | None = "alerts@example.test",
    password: str | None = "hunter2",
    use_starttls: bool = True,
    use_tls: bool = False,
    allow_insecure: bool = False,
) -> EmailNotifier:
    return EmailNotifier(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        from_addr=from_addr,
        to_addrs=to_addrs,
        username=username,
        password=password,
        use_starttls=use_starttls,
        use_tls=use_tls,
        allow_insecure=allow_insecure,
        sender=sender,
    )


class _StubSender:
    """Captures the sent EmailMessage; can raise to simulate failure modes."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.raises = raises
        self.messages: list[EmailMessage] = []

    async def __call__(self, msg: EmailMessage) -> None:
        self.messages.append(msg)
        if self.raises is not None:
            raise self.raises


# Validation ------------------------------------------------------------------


def test_construction_requires_host_port_addrs() -> None:
    with pytest.raises(ValueError, match="smtp_host"):
        EmailNotifier(smtp_host="", smtp_port=587, from_addr="a@b", to_addrs=("c@d",))
    with pytest.raises(ValueError, match="smtp_port"):
        EmailNotifier(smtp_host="h", smtp_port=0, from_addr="a@b", to_addrs=("c@d",))
    with pytest.raises(ValueError, match="from_addr"):
        EmailNotifier(
            smtp_host="h",
            smtp_port=25,
            from_addr="",
            to_addrs=("c@d",),
            allow_insecure=True,
            use_starttls=False,
        )
    with pytest.raises(ValueError, match="to_addrs"):
        EmailNotifier(
            smtp_host="h",
            smtp_port=25,
            from_addr="a@b",
            to_addrs=(),
            allow_insecure=True,
            use_starttls=False,
        )


def test_plain_smtp_rejected_unless_allow_insecure() -> None:
    with pytest.raises(ValueError, match="allow_insecure"):
        EmailNotifier(
            smtp_host="h",
            smtp_port=25,
            from_addr="a@b",
            to_addrs=("c@d",),
            use_starttls=False,
            use_tls=False,
        )


def test_starttls_and_tls_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        EmailNotifier(
            smtp_host="h",
            smtp_port=465,
            from_addr="a@b",
            to_addrs=("c@d",),
            use_starttls=True,
            use_tls=True,
        )


# Send ------------------------------------------------------------------------


async def test_starttls_path_dispatches_multipart_message() -> None:
    sender = _StubSender()
    notifier = _notifier(sender=sender)
    await notifier.send(_alert(), _channel())
    assert len(sender.messages) == 1
    msg = sender.messages[0]
    assert msg["From"] == "alerts@example.test"
    assert msg["To"] == "ops@example.test"
    assert "example.com" in msg["Subject"]
    parts = list(msg.iter_parts())
    types = [p.get_content_type() for p in parts]
    assert "text/plain" in types
    assert "text/html" in types


async def test_auth_failure_raises_permanent() -> None:
    sender = _StubSender(raises=aiosmtplib.SMTPAuthenticationError(535, "bad creds"))
    notifier = _notifier(sender=sender)
    with pytest.raises(NotificationError, match="auth"):
        await notifier.send(_alert(), _channel())


async def test_connect_error_raises_delivery_failed() -> None:
    sender = _StubSender(raises=aiosmtplib.SMTPConnectError("cannot connect"))
    notifier = _notifier(sender=sender)
    with pytest.raises(DeliveryFailedError):
        await notifier.send(_alert(), _channel())


async def test_5xx_response_raises_permanent() -> None:
    sender = _StubSender(raises=aiosmtplib.SMTPResponseException(550, "mailbox unavailable"))
    notifier = _notifier(sender=sender)
    with pytest.raises(NotificationError, match="permanent"):
        await notifier.send(_alert(), _channel())


async def test_4xx_response_raises_transient() -> None:
    sender = _StubSender(raises=aiosmtplib.SMTPResponseException(451, "local error"))
    notifier = _notifier(sender=sender)
    with pytest.raises(DeliveryFailedError):
        await notifier.send(_alert(), _channel())


async def test_timeout_raises_delivery_failed() -> None:
    sender = _StubSender(raises=aiosmtplib.SMTPTimeoutError("read timeout"))
    notifier = _notifier(sender=sender)
    with pytest.raises(DeliveryFailedError):
        await notifier.send(_alert(), _channel())


def test_repr_does_not_leak_password() -> None:
    notifier = EmailNotifier(
        smtp_host="smtp.example.test",
        smtp_port=587,
        from_addr="a@b",
        to_addrs=("c@d",),
        username="user",
        password="super-secret-pw",
    )
    assert "super-secret-pw" not in repr(notifier)


def test_id_classvar() -> None:
    assert EmailNotifier.id == "email"
