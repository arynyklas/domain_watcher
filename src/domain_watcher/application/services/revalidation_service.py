"""``RevalidationService`` — periodic learned-rules health check (ADR 0006 §5).

For each rule whose ``last_revalidated_at + revalidate_after < now``, fetch
fresh WHOIS for ``rule.sample_domain`` and run the same
``ValidationPipeline`` used at learn time. Pass → ``mark_revalidated`` +
``WhoisRuleRevalidated`` event. Fail → ``disable`` + ``WhoisRuleInvalidated``
event with a reason.

The scheduler drives this via ``run_once`` (Phase 8 wiring).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain_watcher.core.parsing.events import (
    WhoisRuleInvalidated,
    WhoisRuleRevalidated,
)
from domain_watcher.core.shared.errors import (
    RuleValidationError,
    SuggestionError,
)

if TYPE_CHECKING:
    from domain_watcher.core.parsing.ports import (
        LearnedRulesRepository,
        ValidationPipeline,
    )
    from domain_watcher.core.parsing.value_objects import LearnedRule
    from domain_watcher.core.shared.events import EventPublisher
    from domain_watcher.core.shared.time_provider import TimeProvider
    from domain_watcher.core.shared.value_objects import DomainName, Duration


WhoisFetcher = Callable[["DomainName"], Awaitable[str]]
"""Async function returning raw WHOIS text for a domain. Implemented as a
checker wrapper in composition; the service keeps no opinion on transport.
"""


@dataclass(frozen=True, slots=True)
class RevalidationService:
    learned_rules: LearnedRulesRepository
    validation_pipeline: ValidationPipeline
    fetch_whois: WhoisFetcher
    publisher: EventPublisher
    clock: TimeProvider
    revalidate_after: Duration

    async def run_once(self) -> None:
        """Revalidate every due learned rule."""
        now = self.clock.now()
        rules = await self.learned_rules.list_all(include_disabled=False)
        for rule in rules:
            if not self._due(rule, now):
                continue
            await self._revalidate(rule)

    def _due(self, rule: LearnedRule, now: object) -> bool:
        # ``now`` is a datetime; typed as object to avoid pulling datetime
        # into the signature header for one comparison.
        from datetime import datetime as _dt

        assert isinstance(now, _dt)
        if rule.last_revalidated_at is None:
            return True
        return (now - rule.last_revalidated_at) >= self.revalidate_after.as_timedelta()

    async def _revalidate(self, rule: LearnedRule) -> None:
        try:
            raw = await self.fetch_whois(rule.sample_domain)
        except Exception as exc:  # transport-level failure: skip, do not demote
            # We do NOT disable on a fetch failure — that would be the same
            # bug as gate-5 transient failure. Leave the rule alone; next
            # cycle will retry.
            _ = exc
            return

        try:
            await self.validation_pipeline.validate(
                rule.as_parse_rule(),
                raw_whois=raw,
                domain=rule.sample_domain,
            )
        except SuggestionError:
            # Transient: do not demote.
            return
        except RuleValidationError as exc:
            await self.learned_rules.disable(rule.id, reason=str(exc) or "validation_failed")
            await self.publisher.publish(
                WhoisRuleInvalidated(
                    occurred_at=self.clock.now(),
                    rule_id=rule.id,
                    tld=rule.tld,
                    reason=str(exc) or "validation_failed",
                )
            )
            return

        at = self.clock.now()
        await self.learned_rules.mark_revalidated(rule.id, at)
        await self.publisher.publish(
            WhoisRuleRevalidated(
                occurred_at=at,
                rule_id=rule.id,
                tld=rule.tld,
            )
        )


__all__ = ["RevalidationService", "WhoisFetcher"]
