"""``ParsingService`` — orchestrates static + learned + LLM-fallback parsing.

This is the heart of ADR 0006. The orchestration order is:

1. Try static rules (from config) via ``WhoisParser.parse``.
2. On ``NoMatchingRuleError``, try learned rules (from
   ``LearnedRulesRepository.for_tld``) via the same parser.
3. On ``NoMatchingRuleError`` again, if a ``RuleSuggester`` is wired AND
   the rate limiters allow, ask the suggester for a candidate. Run the
   ``ValidationPipeline`` against the candidate; on success persist via
   ``LearnedRulesRepository.add`` and re-parse the raw text against the
   freshly-learned rule.
4. On any failure, publish ``ParseFailed`` with a meaningful reason and
   re-raise the appropriate ``ParseError`` subclass.

The two safety knobs are injected as ``RateLimiter`` ports — one keyed
per host (process-wide) and one keyed per TLD (24-hour window). The rate
limit value lives in config; the breaker for transport health lives in
``infrastructure/parsers/safety.py`` (Phase 5) wrapping the suggester.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain_watcher.core.parsing.events import (
    ParseFailed,
    WhoisRuleLearned,
)
from domain_watcher.core.shared.errors import (
    NoMatchingRuleError,
    ParseError,
    RuleValidationError,
    SuggestionError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from domain_watcher.core.parsing.ports import (
        LearnedRulesRepository,
        RuleSuggester,
        ValidationPipeline,
        WhoisParser,
    )
    from domain_watcher.core.parsing.value_objects import ParseRule
    from domain_watcher.core.shared.events import EventPublisher
    from domain_watcher.core.shared.ports import RateLimiter
    from domain_watcher.core.shared.time_provider import TimeProvider
    from domain_watcher.core.shared.value_objects import DomainName


@dataclass(frozen=True, slots=True)
class ParsingService:
    """Static → learned → LLM-fallback parsing pipeline."""

    parser: WhoisParser
    learned_rules: LearnedRulesRepository
    publisher: EventPublisher
    clock: TimeProvider
    static_rules: tuple[ParseRule, ...] = ()
    suggester: RuleSuggester | None = None
    validation_pipeline: ValidationPipeline | None = None
    host_limiter: RateLimiter | None = None
    tld_limiter: RateLimiter | None = None
    suggester_host: str = "default"

    async def parse(self, raw: str, domain: DomainName) -> datetime:
        """Return the parsed expiration datetime for ``raw``."""
        # Step 1: static rules.
        static_for_tld = self._rules_for_tld(self.static_rules, domain.tld)
        if static_for_tld:
            try:
                return await self.parser.parse(raw, domain, static_for_tld)
            except NoMatchingRuleError:
                pass  # fall through to learned

        # Step 2: learned rules.
        learned = await self.learned_rules.for_tld(domain.tld)
        if learned:
            try:
                return await self.parser.parse(raw, domain, learned)
            except NoMatchingRuleError:
                pass  # fall through to LLM

        # Step 3: LLM fallback.
        if self.suggester is None or self.validation_pipeline is None:
            await self._emit_parse_failed(
                domain, reason="no_matching_rule (fallback disabled)", attempted=False
            )
            raise NoMatchingRuleError(
                f"no rule matched for {domain.value} (LLM fallback disabled)"
            )

        # Rate limits — host first, then TLD.
        if self.host_limiter is not None and not await self.host_limiter.acquire(
            self.suggester_host
        ):
            await self._emit_parse_failed(
                domain, reason="rate_limit_host", attempted=False
            )
            raise SuggestionError("rate_limit_host", transient=True)
        if self.tld_limiter is not None and not await self.tld_limiter.acquire(
            domain.tld
        ):
            await self._emit_parse_failed(
                domain, reason="rate_limit_tld", attempted=False
            )
            raise SuggestionError("rate_limit_tld", transient=True)

        # Suggest.
        try:
            candidate = await self.suggester.suggest(raw, domain)
        except SuggestionError as exc:
            await self._emit_parse_failed(
                domain, reason=f"suggestion_error: {exc}", attempted=True
            )
            raise

        # Validate.
        try:
            await self.validation_pipeline.validate(
                candidate, raw_whois=raw, domain=domain
            )
        except SuggestionError as exc:
            # Transient gate-5 failure: do NOT persist; leave for next attempt.
            await self._emit_parse_failed(
                domain, reason=f"validation_transient: {exc}", attempted=True
            )
            raise
        except RuleValidationError as exc:
            await self._emit_parse_failed(
                domain, reason=f"validation_rejected: {exc}", attempted=True
            )
            raise

        # Persist + emit + re-parse.
        sample_sha256 = hashlib.sha256(
            raw.encode("utf-8", errors="replace")
        ).hexdigest()
        new_rule_id = await self.learned_rules.add(
            candidate,
            sample_sha256=sample_sha256,
            sample_domain=domain,
            suggester_id=self.suggester.id,
            pipeline_version=self.validation_pipeline.pipeline_version,
        )
        await self.publisher.publish(
            WhoisRuleLearned(
                occurred_at=self.clock.now(),
                rule_id=new_rule_id,
                tld=candidate.tld,
                sample_domain=domain,
                suggester_id=self.suggester.id,
            )
        )
        try:
            return await self.parser.parse(raw, domain, (candidate,))
        except ParseError as exc:
            await self._emit_parse_failed(
                domain, reason=f"post_learn_parse_failure: {exc}", attempted=True
            )
            raise

    @staticmethod
    def _rules_for_tld(rules: Sequence[ParseRule], tld: str) -> tuple[ParseRule, ...]:
        return tuple(r for r in rules if r.tld == tld)

    async def _emit_parse_failed(
        self,
        domain: DomainName,
        *,
        reason: str,
        attempted: bool,
    ) -> None:
        await self.publisher.publish(
            ParseFailed(
                occurred_at=self.clock.now(),
                domain=domain,
                reason=reason,
                fallback_attempted=attempted,
            )
        )


__all__ = ["ParsingService"]
