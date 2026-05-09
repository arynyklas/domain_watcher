"""``WhoisCheckerWithParser`` — composition of ``_WhoisFetcher`` + ``ParsingService``.

Public registry id is ``"whois"``. ``_WhoisFetcher`` is the internal raw
fetcher; ``ParsingService`` (or any ``WhoisParser``-shaped object) extracts
the expiration date from the WHOIS body.

The composite owns the OK/PERMANENT classification: a successful parse
becomes ``CheckResult.OK``; ``ParseError`` from the parser is mapped to
``PERMANENT_ERROR`` (the parser saw the body, decided no rule applied).
``NoMatchingRuleError`` produced by the parser is also surfaced as
``PERMANENT_ERROR`` — the runtime LLM fallback path lives inside
``ParsingService``, so reaching this point means even the fallback gave up.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.shared.errors import ParseError
from domain_watcher.core.shared.value_objects import DomainName

if TYPE_CHECKING:
    from domain_watcher.infrastructure.checkers.whois import _WhoisFetcher

ParseCallable = Callable[[str, DomainName], Awaitable[datetime]]
"""Async callable that mirrors ``ParsingService.parse``'s signature.

Phase 5 instantiates ``ParsingService`` and passes ``parsing_service.parse``
straight in. Tests inject any compatible coroutine.
"""


@dataclass(slots=True)
class WhoisCheckerWithParser:
    """Public registry entry under id ``"whois"``."""

    id: ClassVar[str] = "whois"

    fetcher: _WhoisFetcher
    parse: ParseCallable

    async def check(self, domain: DomainName) -> CheckResult:
        raw_result = await self.fetcher.fetch(domain)
        if raw_result.outcome is CheckOutcome.PERMANENT_ERROR:
            # NXDOMAIN-style "no match" surfaces straight through with raw.
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                raw=raw_result.raw,
                error=raw_result.error,
            )
        if raw_result.raw is None:
            # Transport-level failure: keep the fetcher's outcome.
            return CheckResult(
                domain=domain,
                outcome=raw_result.outcome,
                expires_at=None,
                source=self.id,
                error=raw_result.error,
            )

        try:
            expires_at = await self.parse(raw_result.raw, domain)
        except ParseError as exc:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                raw=raw_result.raw,
                error=f"parse_error: {exc}",
            )
        return CheckResult(
            domain=domain,
            outcome=CheckOutcome.OK,
            expires_at=expires_at,
            source=self.id,
            raw=raw_result.raw,
        )


__all__ = ["ParseCallable", "WhoisCheckerWithParser"]
