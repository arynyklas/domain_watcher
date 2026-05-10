"""In-memory ``LearnedRulesRepository``.

UNIQUE invariant from ADR 0006 §9 (``UNIQUE (tld, expires_regex)``) is
enforced in ``add``: re-adding the same (tld, regex) raises ``ValueError``.
The store assigns rule ids monotonically starting at 1.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from domain_watcher.core.parsing.value_objects import LearnedRule, RegexPattern

if TYPE_CHECKING:
    from collections.abc import Sequence

    from domain_watcher.core.parsing.value_objects import ParseRule
    from domain_watcher.core.shared.value_objects import DomainName


class MemoryLearnedRulesRepo:
    """Dict-backed learned-rules repo. Lifetime: process."""

    __slots__ = ("_by_id", "_lock", "_next_id")

    def __init__(self) -> None:
        self._by_id: dict[int, LearnedRule] = {}
        self._lock = asyncio.Lock()
        self._next_id = 1

    async def for_tld(self, tld: str) -> Sequence[ParseRule]:
        async with self._lock:
            return tuple(
                lr.as_parse_rule()
                for lr in self._by_id.values()
                if lr.tld == tld and not lr.disabled
            )

    async def add(
        self,
        rule: ParseRule,
        *,
        sample_sha256: str,
        sample_domain: DomainName,
        suggester_id: str,
        pipeline_version: int,
    ) -> int:
        async with self._lock:
            for existing in self._by_id.values():
                if (
                    existing.tld == rule.tld
                    and existing.expires_regex == rule.expires_regex
                    and not existing.disabled
                ):
                    raise ValueError(
                        f"duplicate learned rule for tld={rule.tld!r} regex="
                        f"{rule.expires_regex.raw!r}"
                    )
            rid = self._next_id
            self._next_id += 1
            learned = LearnedRule(
                id=rid,
                tld=rule.tld,
                expires_regex=RegexPattern(rule.expires_regex.raw),
                date_format=rule.date_format,
                timezone=rule.timezone,
                strptime_format=rule.strptime_format,
                auto_learned=True,
                disabled=False,
                suggester_id=suggester_id,
                pipeline_version=pipeline_version,
                sample_whois_sha256=sample_sha256,
                sample_domain=sample_domain,
                created_at=datetime.now(tz=UTC),
                last_revalidated_at=None,
                revalidation_count=0,
            )
            self._by_id[rid] = learned
            return rid

    async def disable(self, rule_id: int, reason: str) -> None:
        async with self._lock:
            existing = self._by_id.get(rule_id)
            if existing is None:
                raise KeyError(f"no learned rule {rule_id}")
            self._by_id[rule_id] = LearnedRule(
                id=existing.id,
                tld=existing.tld,
                expires_regex=existing.expires_regex,
                date_format=existing.date_format,
                timezone=existing.timezone,
                strptime_format=existing.strptime_format,
                auto_learned=existing.auto_learned,
                disabled=True,
                suggester_id=existing.suggester_id,
                pipeline_version=existing.pipeline_version,
                sample_whois_sha256=existing.sample_whois_sha256,
                sample_domain=existing.sample_domain,
                created_at=existing.created_at,
                last_revalidated_at=existing.last_revalidated_at,
                revalidation_count=existing.revalidation_count,
            )
            _ = reason  # reason recorded via WhoisRuleInvalidated event

    async def list_all(
        self,
        *,
        include_disabled: bool = False,
    ) -> Sequence[LearnedRule]:
        async with self._lock:
            return tuple(
                lr for lr in self._by_id.values() if include_disabled or not lr.disabled
            )

    async def mark_revalidated(self, rule_id: int, at: datetime) -> None:
        async with self._lock:
            existing = self._by_id.get(rule_id)
            if existing is None:
                raise KeyError(f"no learned rule {rule_id}")
            self._by_id[rule_id] = LearnedRule(
                id=existing.id,
                tld=existing.tld,
                expires_regex=existing.expires_regex,
                date_format=existing.date_format,
                timezone=existing.timezone,
                strptime_format=existing.strptime_format,
                auto_learned=existing.auto_learned,
                disabled=existing.disabled,
                suggester_id=existing.suggester_id,
                pipeline_version=existing.pipeline_version,
                sample_whois_sha256=existing.sample_whois_sha256,
                sample_domain=existing.sample_domain,
                created_at=existing.created_at,
                last_revalidated_at=at,
                revalidation_count=existing.revalidation_count + 1,
            )


__all__ = ["MemoryLearnedRulesRepo"]
