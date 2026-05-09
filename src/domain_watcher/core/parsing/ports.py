"""Ports (Protocols) for the parsing bounded context (ADR 0002 §4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from domain_watcher.core.parsing.value_objects import (
        LearnedRule,
        ParseRule,
    )
    from domain_watcher.core.shared.value_objects import DomainName


@runtime_checkable
class WhoisParser(Protocol):
    """Deterministic WHOIS-text → expiration extractor.

    Pure: same raw text + rule set → same datetime, always. Implementations
    raise ``NoMatchingRuleError`` or ``ParseError`` on failure.
    """

    async def parse(
        self,
        raw: str,
        domain: DomainName,
        rules: Sequence[ParseRule],
    ) -> datetime: ...


@runtime_checkable
class RuleSuggester(Protocol):
    """Runtime LLM fallback. Produces a candidate ``ParseRule``.

    The plugin neither persists nor validates: that orchestration lives in
    the application-layer ``ParsingService``. A new backend (OpenAI, Anthropic,
    local Ollama, ...) inherits the same safety rails.
    """

    id: ClassVar[str]

    async def suggest(self, raw_whois: str, domain: DomainName) -> ParseRule: ...


@runtime_checkable
class LearnedRulesRepository(Protocol):
    """Operational state: rules learned from ``RuleSuggester`` at runtime."""

    async def for_tld(self, tld: str) -> Sequence[ParseRule]: ...

    async def add(
        self,
        rule: ParseRule,
        *,
        sample_sha256: str,
        sample_domain: DomainName,
        suggester_id: str,
        pipeline_version: int,
    ) -> None: ...

    async def disable(self, rule_id: int, reason: str) -> None: ...

    async def list_all(
        self,
        *,
        include_disabled: bool = False,
    ) -> Sequence[LearnedRule]: ...

    async def mark_revalidated(self, rule_id: int, at: datetime) -> None: ...


__all__ = ["LearnedRulesRepository", "RuleSuggester", "WhoisParser"]
