"""Value objects for the parsing bounded context (ADR 0002 §4, ADR 0006 §9)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from domain_watcher.core.shared.value_objects import DomainName


class DateFormat(StrEnum):
    """Format hints for ``ParseRule.expires_regex`` capture group output."""

    ISO_8601 = "iso8601"
    RFC_3339 = "rfc3339"
    DD_MMM_YYYY = "dd-mmm-yyyy"
    YYYY_MM_DD = "yyyy-mm-dd"
    EPOCH_SECONDS = "epoch"
    CUSTOM = "custom"


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@cache
def _compile_cached(raw: str) -> re.Pattern[str]:
    return re.compile(raw)


@dataclass(frozen=True, slots=True)
class RegexPattern:
    """Eagerly-compiled regex. Bad regex raises ``ValueError`` at construction.

    The compiled pattern is memoized via a module-level ``functools.cache`` so
    every distinct raw pattern is compiled at most once per process — a tiny
    unbounded cache because the set of regexes is finite and small.
    """

    raw: str

    def __post_init__(self) -> None:
        try:
            _compile_cached(self.raw)
        except re.error as exc:
            raise ValueError(f"invalid regex {self.raw!r}: {exc}") from exc

    @property
    def compiled(self) -> re.Pattern[str]:
        return _compile_cached(self.raw)


@dataclass(frozen=True, slots=True)
class ParseRule:
    """A single TLD-keyed regex + date-format rule.

    Invariants enforced in ``__post_init__``:
      - ``tld`` non-empty.
      - ``expires_regex`` has exactly one capture group.
      - ``date_format == CUSTOM`` ⇔ ``strptime_format`` is set.
    """

    tld: str
    expires_regex: RegexPattern
    date_format: DateFormat
    timezone: str = "UTC"
    strptime_format: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tld, str) or not self.tld.strip():
            raise ValueError("ParseRule.tld must be a non-empty string")
        groups = self.expires_regex.compiled.groups
        if groups != 1:
            raise ValueError(
                "ParseRule.expires_regex must have exactly one capture group, "
                f"got {groups}"
            )
        custom = self.date_format is DateFormat.CUSTOM
        has_strptime = self.strptime_format is not None
        if custom != has_strptime:
            raise ValueError(
                "ParseRule.strptime_format is required iff date_format == CUSTOM"
            )


@dataclass(frozen=True, slots=True)
class LearnedRule:
    """Persisted runtime-learned rule + provenance metadata (ADR 0006 §9).

    Mirrors the ``learned_rules`` SQL table, minus orm-side columns.
    """

    id: int
    tld: str
    expires_regex: RegexPattern
    date_format: DateFormat
    timezone: str
    strptime_format: str | None
    auto_learned: bool
    disabled: bool
    suggester_id: str
    pipeline_version: int
    sample_whois_sha256: str
    sample_domain: DomainName
    created_at: datetime
    last_revalidated_at: datetime | None
    revalidation_count: int

    def __post_init__(self) -> None:
        if not self.tld:
            raise ValueError("LearnedRule.tld is required")
        if not _SHA256_HEX.match(self.sample_whois_sha256):
            raise ValueError(
                "LearnedRule.sample_whois_sha256 must be 64 lowercase hex chars"
            )
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("LearnedRule.created_at must be tz-aware UTC")
        if self.last_revalidated_at is not None:
            tz = self.last_revalidated_at.tzinfo
            if tz is None or tz.utcoffset(self.last_revalidated_at) is None:
                raise ValueError("LearnedRule.last_revalidated_at must be tz-aware UTC")
        if self.revalidation_count < 0:
            raise ValueError(
                f"LearnedRule.revalidation_count must be >= 0, "
                f"got {self.revalidation_count}"
            )
        if self.pipeline_version < 1:
            raise ValueError(
                f"LearnedRule.pipeline_version must be >= 1, "
                f"got {self.pipeline_version}"
            )
        if not self.suggester_id:
            raise ValueError("LearnedRule.suggester_id is required")
        custom = self.date_format is DateFormat.CUSTOM
        has_strptime = self.strptime_format is not None
        if custom != has_strptime:
            raise ValueError(
                "LearnedRule.strptime_format is required iff date_format == CUSTOM"
            )

    def as_parse_rule(self) -> ParseRule:
        """Project the static fields to a runtime-applicable ``ParseRule``."""
        return ParseRule(
            tld=self.tld,
            expires_regex=self.expires_regex,
            date_format=self.date_format,
            timezone=self.timezone,
            strptime_format=self.strptime_format,
        )


__all__ = [
    "DateFormat",
    "LearnedRule",
    "ParseRule",
    "RegexPattern",
]
