"""Event-bus subscriber that ticks the Prometheus counters.

Wired by the composition root: ``bus.on(EventType, handler)`` for each
event the metrics module cares about. Keeping the wiring in one place
(this module) means call sites never reach for ``prometheus_client``.

The functions are coroutines so they fit the bus's ``async`` handler
signature; they do no I/O — counter increments are synchronous and
cheap, but the awaitable shape is what the bus expects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain_watcher.core.checking.events import DomainCheckCompleted, DomainCheckFailed
from domain_watcher.core.notification.events import NotificationDispatched
from domain_watcher.core.parsing.events import WhoisRuleInvalidated, WhoisRuleLearned
from domain_watcher.infrastructure.observability.metrics import (
    alerts_sent_total,
    checks_total,
    rules_invalidated_total,
    rules_learned_total,
)

if TYPE_CHECKING:
    from domain_watcher.application.event_bus import InProcessEventBus


async def _on_check_completed(event: DomainCheckCompleted) -> None:
    result = event.result
    if result is None:
        # ``DomainCheckCompleted.__post_init__`` rejects ``None`` at construction,
        # so this branch is reachable only if that invariant ever changes;
        # defensively skip the metric instead of crashing the bus.
        return
    checks_total.labels(checker=result.source, outcome=result.outcome.value).inc()


async def _on_check_failed(event: DomainCheckFailed) -> None:
    outcome = "transient_error" if event.transient else "permanent_error"
    checks_total.labels(checker=event.source, outcome=outcome).inc()


async def _on_dispatched(event: NotificationDispatched) -> None:
    if event.alert is None or event.channel is None:
        # See ``_on_check_completed`` — invariant from event ``__post_init__``.
        return
    alerts_sent_total.labels(
        channel=event.channel.value,
        severity=event.alert.severity.value,
    ).inc()


async def _on_rule_learned(event: WhoisRuleLearned) -> None:
    rules_learned_total.labels(tld=event.tld, suggester=event.suggester_id).inc()


async def _on_rule_invalidated(event: WhoisRuleInvalidated) -> None:
    rules_invalidated_total.labels(tld=event.tld, reason=event.reason).inc()


def register(bus: InProcessEventBus) -> None:
    """Subscribe metrics handlers to the relevant events on ``bus``.

    Idempotent at the bus level: calling twice double-counts, so the
    composition root MUST call this exactly once. Tests can build a
    fresh bus per case to avoid coupling.
    """

    bus.on(DomainCheckCompleted, _on_check_completed)
    bus.on(DomainCheckFailed, _on_check_failed)
    bus.on(NotificationDispatched, _on_dispatched)
    bus.on(WhoisRuleLearned, _on_rule_learned)
    bus.on(WhoisRuleInvalidated, _on_rule_invalidated)


__all__ = ["register"]
