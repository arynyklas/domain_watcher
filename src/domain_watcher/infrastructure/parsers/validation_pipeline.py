"""6-gate ``ValidationPipeline`` from ADR 0006 §4.

Every suggested ``ParseRule`` runs through these gates in order:

1. Compile + exactly-one-capture-group (already a ``RegexPattern``/``ParseRule``
   invariant; this is a defence-in-depth re-check).
2. Match the *same* WHOIS text the LLM saw.
3. Parse the captured string to a tz-aware ``datetime`` per ``date_format``.
4. Range check: parsed value must be in the future and within
   ``max_age_years``; reject if it equals a registration-date heuristic
   line in the same WHOIS body.
5. Cross-check against a known-good domain in the same TLD. Cached for
   ``revalidate_after`` to avoid two WHOIS fetches per learn attempt.
   A *transient* cross-check failure raises
   ``SuggestionError(transient=True)`` (callers retry); the rule is
   neither accepted nor rejected. A missing known-good entry skips this
   gate and increments
   ``domain_watcher_pipeline_gate5_skipped_total{reason="no_known_good"}``.
6. Operator-hint check: reject suspiciously round/sentinel dates
   (e.g. ``1970-01-01``) that LLM defaults sometimes produce.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from domain_watcher.core.shared.errors import (
    ParseError,
    PermanentCheckError,
    RuleValidationError,
    SuggestionError,
    TransientCheckError,
)
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.parsers._metrics import pipeline_gate5_skipped_total
from domain_watcher.infrastructure.parsers.regex import _convert

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from domain_watcher.core.parsing.value_objects import ParseRule
    from domain_watcher.core.shared.time_provider import TimeProvider


@runtime_checkable
class CrossCheckFetcher(Protocol):
    """Fetches raw WHOIS text for the gate-5 known-good cross check.

    Adapters (typically a thin wrapper around ``_WhoisFetcher``) MUST raise
    ``TransientCheckError`` on transport flakes and ``PermanentCheckError``
    on authoritative no-such-domain. Returning empty / blank text is
    treated as a transient failure by the pipeline.
    """

    async def fetch_raw(self, domain: DomainName) -> str: ...


# Detects the "Registered"/"Created" line on common WHOIS shapes.
_REGISTRATION_LINE_RE = re.compile(
    r"^\s*(?:Registered\s+on|Registered|Creation\s+Date|Created|created):\s*(\S+)",
    re.IGNORECASE | re.MULTILINE,
)

# Sentinel / obviously-bogus dates the LLM sometimes hands back.
_SENTINEL_DATES: frozenset[datetime] = frozenset(
    {
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC),
        datetime(1900, 1, 1, tzinfo=UTC),
        datetime(2000, 1, 1, tzinfo=UTC),
        datetime(9999, 12, 31, tzinfo=UTC),
    }
)


def _load_known_good() -> Mapping[str, Sequence[str]]:
    """Load embedded ``known_good_domains.json``.

    The module ships in ``infrastructure/parsers/data/`` next to this file.
    """
    path = Path(__file__).with_name("data") / "known_good_domains.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_tlds = payload.get("tlds", {})
    return {tld: tuple(domains) for tld, domains in raw_tlds.items()}


@dataclass(slots=True)
class ValidationPipeline:
    """Six-gate safety pipeline over an LLM-suggested ``ParseRule``."""

    pipeline_version: ClassVar[int] = 1

    cross_check_fetcher: CrossCheckFetcher
    clock: TimeProvider
    max_age_years: int = 50
    revalidate_after_seconds: int = 30 * 86400
    known_good: Mapping[str, Sequence[str]] = field(default_factory=_load_known_good)
    _cache: dict[tuple[str, str], tuple[datetime, str]] = field(default_factory=dict)

    async def validate(
        self,
        rule: ParseRule,
        *,
        raw_whois: str,
        domain: DomainName,
    ) -> None:
        # Gate 1: compile + exactly one group. ParseRule construction already
        # enforces both, but a malformed plugin cannot bypass us by handing
        # in a hand-built rule that lies — so we re-check.
        compiled = rule.expires_regex.compiled
        if compiled.groups != 1:
            raise RuleValidationError(
                f"gate1: regex must have exactly one capture group, has {compiled.groups}"
            )

        # Gate 2: match the same WHOIS the LLM saw.
        match = compiled.search(raw_whois)
        if match is None:
            raise RuleValidationError(
                "gate2: rule did not match the WHOIS text it was suggested for"
            )
        captured = match.group(1)

        # Gate 3: parse to a tz-aware datetime.
        try:
            parsed = _convert(captured, rule)
        except ParseError as exc:
            raise RuleValidationError(
                f"gate3: captured value {captured!r} did not parse: {exc}"
            ) from exc

        # Gate 4: range + registration-heuristic check.
        now = self.clock.now()
        try:
            max_future = now + timedelta(days=365 * self.max_age_years)
        except OverflowError:
            max_future = datetime.max.replace(tzinfo=UTC)
        if parsed <= now:
            raise RuleValidationError(
                f"gate4: parsed expiration {parsed.isoformat()} is not in the future"
            )
        if parsed > max_future:
            raise RuleValidationError(
                f"gate4: parsed expiration {parsed.isoformat()} is more than "
                f"{self.max_age_years}y in the future"
            )
        for reg_match in _REGISTRATION_LINE_RE.finditer(raw_whois):
            try:
                reg_parsed = _convert(reg_match.group(1), rule)
            except ParseError:
                continue
            if reg_parsed == parsed:
                raise RuleValidationError(
                    "gate4: rule extracted the registration date, not the expiration"
                )

        # Gate 5: cross-check against a known-good domain.
        await self._gate5(rule, domain)

        # Gate 6: operator-hint sentinel check.
        if parsed in _SENTINEL_DATES:
            raise RuleValidationError(
                f"gate6: parsed value {parsed.isoformat()} is a sentinel/default date"
            )

    async def _gate5(self, rule: ParseRule, domain: DomainName) -> None:
        candidates = self.known_good.get(domain.tld, ())
        kg_domain: DomainName | None = None
        for candidate in candidates:
            d = DomainName(candidate)
            if d.value != domain.value:
                kg_domain = d
                break
        if kg_domain is None:
            pipeline_gate5_skipped_total.inc("no_known_good")
            return

        cache_key = (domain.tld, kg_domain.value)
        cached = self._cache.get(cache_key)
        now = self.clock.now()
        if cached is not None and (now - cached[0]).total_seconds() < self.revalidate_after_seconds:
            raw_kg = cached[1]
        else:
            try:
                raw_kg = await self.cross_check_fetcher.fetch_raw(kg_domain)
            except TransientCheckError as exc:
                pipeline_gate5_skipped_total.inc("cross_check_unavailable")
                raise SuggestionError(
                    f"gate5: cross-check transient ({exc})", transient=True
                ) from exc
            except PermanentCheckError:
                # Authoritative fetch failure on the known-good is rare and
                # not the rule's fault; record + skip.
                pipeline_gate5_skipped_total.inc("cross_check_unavailable")
                return
            if not raw_kg.strip():
                pipeline_gate5_skipped_total.inc("cross_check_unavailable")
                raise SuggestionError(
                    f"gate5: cross-check returned empty body for {kg_domain.value}",
                    transient=True,
                )
            self._cache[cache_key] = (now, raw_kg)

        if rule.expires_regex.compiled.search(raw_kg) is None:
            raise RuleValidationError(
                f"gate5: rule did not match known-good {kg_domain.value} WHOIS — "
                "likely overfit to one sample"
            )


__all__ = ["CrossCheckFetcher", "ValidationPipeline"]
