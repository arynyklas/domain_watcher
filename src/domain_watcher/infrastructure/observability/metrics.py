"""Prometheus metrics + ``/metrics`` HTTP listener.

Counters/gauges live in module scope so call sites can ``increment`` /
``observe`` without threading a registry around. The listener is opt-in
via ``runtime.metrics.enabled`` and uses :mod:`aiohttp` (ADR 0001 §11(3)
— no FastAPI dependency).

Metric names follow the convention from ADR 0001 §3:
``domain_watcher_<thing>_<unit>{labels}``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiohttp import web
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# A dedicated registry so tests can pass their own; production wiring uses
# this same module-level instance.
REGISTRY = CollectorRegistry(auto_describe=True)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------


alerts_sent_total = Counter(
    "domain_watcher_alerts_sent_total",
    "Number of alerts successfully delivered to a channel.",
    labelnames=("channel", "severity"),
    registry=REGISTRY,
)

checks_total = Counter(
    "domain_watcher_checks_total",
    "Number of expiration checks performed.",
    labelnames=("checker", "outcome"),
    registry=REGISTRY,
)

monitored_domains = Gauge(
    "domain_watcher_monitored_domains",
    "Number of domains currently being monitored.",
    registry=REGISTRY,
)

check_duration_seconds = Histogram(
    "domain_watcher_check_duration_seconds",
    "Wall-clock duration of a single expiration check.",
    labelnames=("checker",),
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

rules_learned_total = Counter(
    "domain_watcher_rules_learned_total",
    "Number of WHOIS rules accepted by the validation pipeline.",
    labelnames=("tld", "suggester"),
    registry=REGISTRY,
)

rules_invalidated_total = Counter(
    "domain_watcher_rules_invalidated_total",
    "Number of learned rules disabled by the revalidation job.",
    labelnames=("tld", "reason"),
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# /metrics listener
# ---------------------------------------------------------------------------


async def _metrics_handler(_request: web.Request) -> web.Response:
    body = generate_latest(REGISTRY)
    # CONTENT_TYPE_LATEST may contain a `; charset=...` suffix that aiohttp
    # rejects when passed verbatim to ``content_type``; split off the type.
    content_type = CONTENT_TYPE_LATEST.split(";")[0].strip()
    return web.Response(body=body, content_type=content_type)


def build_app() -> web.Application:
    """Return an aiohttp app exposing only ``GET /metrics``."""

    app = web.Application()
    app.router.add_get("/metrics", _metrics_handler)
    return app


class MetricsServer:
    """Lifecycle wrapper around the aiohttp ``/metrics`` listener.

    Call :meth:`start` once during startup and :meth:`stop` during
    shutdown. ``start`` is a no-op when the server is already running so
    hot-reload subscribers can call it without checking state.
    """

    __slots__ = ("_app", "_host", "_port", "_runner", "_site")

    def __init__(self, *, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def running(self) -> bool:
        return self._site is not None

    async def start(self) -> None:
        if self._site is not None:
            return
        self._app = build_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self._host, port=self._port)
        await self._site.start()
        _log.info(
            "metrics_listener_started",
            extra={"host": self._host, "port": self._port},
        )

    async def stop(self) -> None:
        site, runner = self._site, self._runner
        self._site = None
        self._runner = None
        self._app = None
        if site is not None:
            with contextlib.suppress(RuntimeError, asyncio.CancelledError):
                await site.stop()
        if runner is not None:
            with contextlib.suppress(RuntimeError, asyncio.CancelledError):
                await runner.cleanup()


__all__ = [
    "REGISTRY",
    "MetricsServer",
    "alerts_sent_total",
    "build_app",
    "check_duration_seconds",
    "checks_total",
    "monitored_domains",
    "rules_invalidated_total",
    "rules_learned_total",
]
