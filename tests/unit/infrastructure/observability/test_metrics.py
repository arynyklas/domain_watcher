"""Unit tests for metrics + structlog scrubber (Task 11.3)."""

from __future__ import annotations

import socket

import aiohttp
import pytest
from prometheus_client import CollectorRegistry, Counter, generate_latest

from domain_watcher.infrastructure.observability import metrics
from domain_watcher.infrastructure.observability.metrics import (
    REGISTRY,
    MetricsServer,
    alerts_sent_total,
    checks_total,
    monitored_domains,
)
from domain_watcher.infrastructure.observability.structlog_setup import scrub_secrets

# ---------- Metric counters ------------------------------------------------


def _read_metric(name: str, **labels: str) -> float:
    """Return the current value of a labelled metric."""

    body = generate_latest(REGISTRY).decode()
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    needle = f"{name}{{{label_str}}} "
    for line in body.splitlines():
        if line.startswith(needle):
            return float(line.removeprefix(needle))
    return 0.0


def test_alerts_sent_counter_ticks() -> None:
    before = _read_metric(
        "domain_watcher_alerts_sent_total", channel="telegram", severity="warning"
    )
    alerts_sent_total.labels(channel="telegram", severity="warning").inc()
    alerts_sent_total.labels(channel="telegram", severity="warning").inc(2)
    after = _read_metric(
        "domain_watcher_alerts_sent_total", channel="telegram", severity="warning"
    )
    assert after == before + 3


def test_checks_counter_separates_outcomes() -> None:
    checks_total.labels(checker="rdap", outcome="ok").inc()
    checks_total.labels(checker="rdap", outcome="permanent_error").inc()
    ok = _read_metric("domain_watcher_checks_total", checker="rdap", outcome="ok")
    bad = _read_metric(
        "domain_watcher_checks_total", checker="rdap", outcome="permanent_error"
    )
    assert ok >= 1
    assert bad >= 1


def test_monitored_domains_gauge_set() -> None:
    monitored_domains.set(7)
    body = generate_latest(REGISTRY).decode()
    assert "domain_watcher_monitored_domains 7.0" in body


# ---------- /metrics HTTP listener ----------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.asyncio
async def test_metrics_server_serves_endpoint() -> None:
    port = _free_port()
    server = MetricsServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        # Tick a counter so the body has at least one non-trivial line.
        alerts_sent_total.labels(channel="email", severity="critical").inc()
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}/metrics") as resp,
        ):
            assert resp.status == 200
            body = await resp.text()
        assert "domain_watcher_alerts_sent_total" in body
        # Only /metrics is wired — anything else is 404.
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}/other") as resp,
        ):
            assert resp.status == 404
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_metrics_server_start_is_idempotent() -> None:
    port = _free_port()
    server = MetricsServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        await server.start()  # second call MUST be a no-op
        assert server.running is True
    finally:
        await server.stop()
        assert server.running is False


@pytest.mark.asyncio
async def test_metrics_server_stop_when_not_started_is_safe() -> None:
    server = MetricsServer(host="127.0.0.1", port=_free_port())
    await server.stop()  # MUST NOT raise


# ---------- Secret scrubber ----------------------------------------------


def test_scrubber_redacts_known_secret_keys() -> None:
    out = scrub_secrets(
        None,
        "info",
        {
            "event": "send",
            "bot_token": "1234:ABCDEF",
            "api_key": "sk-zzz",
            "password": "hunter2",
            "secret": "shhh",
            "token": "raw",
            "Authorization": "Bearer xyz",  # case-insensitive
        },
    )
    assert out["bot_token"] == "***"
    assert out["api_key"] == "***"
    assert out["password"] == "***"
    assert out["secret"] == "***"
    assert out["token"] == "***"
    assert out["Authorization"] == "***"
    assert out["event"] == "send"


def test_scrubber_recurses_into_nested_dicts() -> None:
    out = scrub_secrets(
        None,
        "info",
        {
            "settings": {"bot_token": "abc", "chat_id": "100"},
        },
    )
    assert out["settings"] == {"bot_token": "***", "chat_id": "100"}


def test_scrubber_strips_url_userinfo_and_query() -> None:
    out = scrub_secrets(
        None,
        "info",
        {
            "webhook_url": "https://user:pass@hooks.example.com:9000/abc?token=xyz",
            "api_base": "https://api.test/v1?key=secret",
        },
    )
    assert out["webhook_url"] == "https://hooks.example.com:9000"
    assert out["api_base"] == "https://api.test"


def test_scrubber_passes_through_non_secret_strings() -> None:
    out = scrub_secrets(None, "info", {"domain": "example.com", "count": 3})
    assert out == {"domain": "example.com", "count": 3}


def test_scrubber_handles_lists_and_tuples() -> None:
    out = scrub_secrets(
        None,
        "info",
        {
            "channels": [{"bot_token": "abc"}, {"name": "ok"}],
            "tuple_data": ({"password": "x"},),
        },
    )
    assert out["channels"] == [{"bot_token": "***"}, {"name": "ok"}]
    assert out["tuple_data"] == ({"password": "***"},)


# ---------- Use a private REGISTRY for isolation in CI ---------------------


def test_metric_definitions_are_in_module_registry() -> None:
    """The shared registry MUST own every metric this module exports."""

    body = generate_latest(REGISTRY).decode()
    for name in (
        "domain_watcher_alerts_sent_total",
        "domain_watcher_checks_total",
        "domain_watcher_monitored_domains",
        "domain_watcher_check_duration_seconds",
        "domain_watcher_rules_learned_total",
        "domain_watcher_rules_invalidated_total",
    ):
        assert name in body, f"missing metric {name}"


def test_user_registry_can_be_reset_for_isolation() -> None:
    """Plugin authors can build their own registry without poisoning ours."""

    user_reg = CollectorRegistry()
    counter = Counter("custom_total", "demo", labelnames=("k",), registry=user_reg)
    counter.labels(k="x").inc()
    body = generate_latest(user_reg).decode()
    assert "custom_total" in body
    # Our registry MUST NOT see it.
    assert "custom_total" not in generate_latest(REGISTRY).decode()


def test_module_REGISTRY_is_singleton() -> None:
    """Imports return the same registry instance — call sites share counters."""

    from domain_watcher.infrastructure.observability import metrics as m2

    assert m2.REGISTRY is metrics.REGISTRY
