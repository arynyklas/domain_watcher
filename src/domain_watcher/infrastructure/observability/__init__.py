"""Logging + Prometheus metrics for the standalone daemon."""

from __future__ import annotations

from domain_watcher.infrastructure.observability.metrics import (
    REGISTRY,
    MetricsServer,
    alerts_sent_total,
    build_app,
    check_duration_seconds,
    checks_total,
    monitored_domains,
    rules_invalidated_total,
    rules_learned_total,
)
from domain_watcher.infrastructure.observability.structlog_setup import (
    configure,
    scrub_secrets,
)

__all__ = [
    "REGISTRY",
    "MetricsServer",
    "alerts_sent_total",
    "build_app",
    "check_duration_seconds",
    "checks_total",
    "configure",
    "monitored_domains",
    "rules_invalidated_total",
    "rules_learned_total",
    "scrub_secrets",
]
