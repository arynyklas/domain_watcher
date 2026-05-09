"""``RegexWhoisParser`` — deterministic ``WhoisParser`` adapter.

Implements the ``WhoisParser`` Protocol from
``core/parsing/ports.py``. Iterates supplied rules, applies the first
that matches the domain's TLD and whose regex captures, then converts
the captured string into a tz-aware UTC ``datetime`` according to the
rule's ``date_format`` and ``timezone``.

Failure modes:

- No rule had a matching TLD or regex match → ``NoMatchingRuleError``.
- A rule matched but the captured string could not be parsed under its
  declared ``date_format`` → ``ParseError``.

The parser does **not** trigger any LLM fallback: that orchestration
lives in ``application/services/parsing_service.py`` which catches
``NoMatchingRuleError`` from this parser and decides what to do next.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from domain_watcher.core.parsing.value_objects import DateFormat
from domain_watcher.core.shared.errors import NoMatchingRuleError, ParseError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from domain_watcher.core.parsing.value_objects import ParseRule
    from domain_watcher.core.shared.value_objects import DomainName


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip


def _resolve_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ParseError(f"unknown timezone {name!r}") from exc


def _parse_iso_like(captured: str) -> datetime:
    # Accept trailing 'Z' as +00:00; defer to fromisoformat otherwise.
    raw = captured.replace("Z", "+00:00") if captured.endswith("Z") else captured
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ParseError(f"unparseable ISO/RFC3339 datetime {captured!r}: {exc}") from exc


def _parse_yyyy_mm_dd(captured: str) -> datetime:
    try:
        return datetime.strptime(captured.strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise ParseError(f"unparseable yyyy-mm-dd {captured!r}: {exc}") from exc


def _parse_dd_mmm_yyyy(captured: str) -> datetime:
    s = captured.strip()
    parts = s.split("-")
    if len(parts) != 3:
        raise ParseError(f"unparseable dd-mmm-yyyy {captured!r}")
    day_s, mon_s, year_s = parts
    try:
        day = int(day_s)
        year = int(year_s)
    except ValueError as exc:
        raise ParseError(f"unparseable dd-mmm-yyyy {captured!r}: {exc}") from exc
    month = _MONTHS.get(mon_s.lower()[:3])
    if month is None:
        raise ParseError(f"unknown month abbreviation in {captured!r}")
    try:
        return datetime(year=year, month=month, day=day)
    except ValueError as exc:
        raise ParseError(f"invalid dd-mmm-yyyy date {captured!r}: {exc}") from exc


def _parse_epoch(captured: str) -> datetime:
    try:
        seconds = int(captured.strip())
    except ValueError as exc:
        raise ParseError(f"unparseable epoch seconds {captured!r}: {exc}") from exc
    return datetime.fromtimestamp(seconds, tz=UTC)


def _parse_custom(captured: str, fmt: str) -> datetime:
    try:
        return datetime.strptime(captured.strip(), fmt)
    except ValueError as exc:
        raise ParseError(
            f"unparseable {captured!r} under custom strptime format {fmt!r}: {exc}"
        ) from exc


def _to_utc(parsed: datetime, tz_name: str) -> datetime:
    if parsed.tzinfo is None:
        zone = _resolve_zone(tz_name)
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(UTC)


def _convert(captured: str, rule: ParseRule) -> datetime:
    df = rule.date_format
    if df is DateFormat.ISO_8601 or df is DateFormat.RFC_3339:
        parsed = _parse_iso_like(captured.strip())
    elif df is DateFormat.YYYY_MM_DD:
        parsed = _parse_yyyy_mm_dd(captured)
    elif df is DateFormat.DD_MMM_YYYY:
        parsed = _parse_dd_mmm_yyyy(captured)
    elif df is DateFormat.EPOCH_SECONDS:
        # Already UTC; epoch is timezone-anchored by definition.
        return _parse_epoch(captured)
    elif df is DateFormat.CUSTOM:
        if rule.strptime_format is None:  # pragma: no cover — VO invariant
            raise ParseError("custom date_format requires strptime_format")
        parsed = _parse_custom(captured, rule.strptime_format)
    else:  # pragma: no cover — exhaustive
        raise ParseError(f"unsupported date_format {df!r}")
    return _to_utc(parsed, rule.timezone)


@dataclass(frozen=True, slots=True)
class RegexWhoisParser:
    """Stateless regex-driven ``WhoisParser``."""

    id: ClassVar[str] = "regex"

    async def parse(
        self,
        raw: str,
        domain: DomainName,
        rules: Sequence[ParseRule],
    ) -> datetime:
        last_parse_error: ParseError | None = None
        attempted = False
        for rule in rules:
            if rule.tld != domain.tld:
                continue
            attempted = True
            match = rule.expires_regex.compiled.search(raw)
            if match is None:
                continue
            captured = match.group(1)
            try:
                return _convert(captured, rule)
            except ParseError as exc:
                # Remember and keep trying — a later rule may succeed.
                last_parse_error = exc
                continue
        if last_parse_error is not None:
            raise last_parse_error
        if not attempted:
            raise NoMatchingRuleError(f"no parse rule for tld {domain.tld!r}")
        raise NoMatchingRuleError(
            f"no parse rule matched WHOIS body for {domain.value} (tld={domain.tld})"
        )


__all__ = ["RegexWhoisParser"]
