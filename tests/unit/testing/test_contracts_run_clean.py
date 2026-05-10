"""Meta-test: built-in adapters satisfy the published contract harnesses.

If this test ever goes red, ``domain_watcher.testing`` has drifted from
the implementations it claims to certify. That is a public-API break.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.shared.errors import DeliveryFailedError
from domain_watcher.infrastructure.notifiers.discord import DiscordNotifier
from domain_watcher.infrastructure.notifiers.telegram import TelegramNotifier
from domain_watcher.infrastructure.notifiers.webhook import WebhookNotifier
from domain_watcher.testing import (
    CheckerContractTest,
    MemoryIdempotencyStore,
    MemoryLearnedRulesRepo,
    MemoryMonitoredDomainRepo,
    PluginContractTest,
    RepoContractTest,
)

# ---------- Notifier contract ------------------------------------------------


def _client(*, ok: bool) -> httpx.AsyncClient:
    if ok:

        def handler(req: httpx.Request) -> httpx.Response:
            if "discord" in req.url.host:
                return httpx.Response(204)
            return httpx.Response(200, json={"ok": True, "result": {}})
    else:

        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("transport down", request=req)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestTelegramNotifierContract(PluginContractTest):
    """Built-in Telegram adapter satisfies the public contract."""

    def make_ok(self) -> Any:
        return TelegramNotifier(
            bot_token="t0k3n", chat_id="100", client=_client(ok=True)
        )

    def make_failing(self) -> Any:
        return TelegramNotifier(
            bot_token="t0k3n", chat_id="100", client=_client(ok=False)
        )


class TestDiscordNotifierContract(PluginContractTest):
    def make_ok(self) -> Any:
        return DiscordNotifier(
            webhook_url="https://discord.test/api/webhooks/1/abc",
            client=_client(ok=True),
        )

    def make_failing(self) -> Any:
        return DiscordNotifier(
            webhook_url="https://discord.test/api/webhooks/1/abc",
            client=_client(ok=False),
        )


class TestWebhookNotifierContract(PluginContractTest):
    def make_ok(self) -> Any:
        return WebhookNotifier(
            url="https://hook.test/",
            body_template="${domain}",
            client=_client(ok=True),
        )

    def make_failing(self) -> Any:
        return WebhookNotifier(
            url="https://hook.test/",
            body_template="${domain}",
            client=_client(ok=False),
        )


# ---------- Checker contract -------------------------------------------------


class _StubChecker:
    """Tiny checker used to exercise the checker contract harness."""

    id = "stub"

    def __init__(self, *, outcome: CheckOutcome, error: str | None = None) -> None:
        self._outcome = outcome
        self._error = error

    async def check(self, domain: Any) -> CheckResult:
        if self._outcome == CheckOutcome.OK:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.OK,
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
                source=self.id,
            )
        return CheckResult(
            domain=domain,
            outcome=self._outcome,
            expires_at=None,
            source=self.id,
            error=self._error or "stub",
        )


class TestStubCheckerContract(CheckerContractTest):
    """Smoke-test the checker contract suite against a deterministic stub."""

    def make_ok(self) -> Any:
        return _StubChecker(outcome=CheckOutcome.OK)

    def make_transient(self) -> Any:
        return _StubChecker(
            outcome=CheckOutcome.TRANSIENT_ERROR, error="transport blip"
        )

    def make_permanent(self) -> Any:
        return _StubChecker(
            outcome=CheckOutcome.PERMANENT_ERROR, error="no such domain"
        )


# ---------- Repo contract ----------------------------------------------------


class TestMemoryRepoContract(RepoContractTest):
    """Built-in memory repos satisfy the public repo contract."""

    @asynccontextmanager
    async def make_repos(self) -> AsyncIterator[tuple[Any, Any, Any]]:
        yield (
            MemoryMonitoredDomainRepo(),
            MemoryLearnedRulesRepo(),
            MemoryIdempotencyStore(),
        )


# ---------- Sanity test: the harness rejects mis-classified failures --------


class _AlwaysOkChecker:
    id = "always-ok"

    async def check(self, domain: Any) -> CheckResult:
        return CheckResult(
            domain=domain,
            outcome=CheckOutcome.OK,
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            source=self.id,
        )


@pytest.mark.asyncio
async def test_checker_contract_rejects_wrong_classification() -> None:
    """A checker that returns OK when it should fail MUST fail the contract."""

    class _Bad(CheckerContractTest):
        def make_ok(self) -> Any:
            return _AlwaysOkChecker()

        def make_transient(self) -> Any:
            return _AlwaysOkChecker()  # wrong: should fail with TRANSIENT_ERROR

        def make_permanent(self) -> Any:
            return _AlwaysOkChecker()

    with pytest.raises(AssertionError):
        await _Bad().test_transient_failure_classifies_correctly()


@pytest.mark.asyncio
async def test_notifier_contract_rejects_silent_dedup() -> None:
    """A notifier that internally raises on the second send MUST fail the contract."""

    class _DedupingNotifier:
        id = "dedup"

        def __init__(self) -> None:
            self._sent = False

        async def send(self, _alert: object, _channel: object) -> None:
            if self._sent:
                raise DeliveryFailedError("internal dedup")
            self._sent = True

    class _Bad(PluginContractTest):
        def make_ok(self) -> Any:
            return _DedupingNotifier()

        def make_failing(self) -> Any:
            return _DedupingNotifier()

    with pytest.raises(DeliveryFailedError):
        await _Bad().test_send_is_at_least_once_safe()
