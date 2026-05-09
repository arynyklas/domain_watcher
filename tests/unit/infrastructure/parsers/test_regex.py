"""``RegexWhoisParser`` — fixture-driven tests for each canonical TLD."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.errors import NoMatchingRuleError, ParseError
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.parsers.regex import RegexWhoisParser

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "whois"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


COM_RULE = ParseRule(
    tld="com",
    expires_regex=RegexPattern(r"Registry Expiry Date:\s*(\S+)"),
    date_format=DateFormat.ISO_8601,
)
RU_RULE = ParseRule(
    tld="ru",
    expires_regex=RegexPattern(r"paid-till:\s*(\S+)"),
    date_format=DateFormat.ISO_8601,
)
CO_UK_RULE = ParseRule(
    tld="co.uk",
    expires_regex=RegexPattern(r"Expiry date:\s*(\d{2}-[A-Za-z]{3}-\d{4})"),
    date_format=DateFormat.DD_MMM_YYYY,
    timezone="Europe/London",
)
APP_RULE = ParseRule(
    tld="app",
    expires_regex=RegexPattern(r"Registry Expiry Date:\s*(\S+)"),
    date_format=DateFormat.ISO_8601,
)
IO_RULE = ParseRule(
    tld="io",
    expires_regex=RegexPattern(r"Registry Expiry Date:\s*(\S+)"),
    date_format=DateFormat.ISO_8601,
)


@pytest.mark.parametrize(
    ("fixture", "domain", "rule", "expected"),
    [
        (
            "example.com.txt",
            "example.com",
            COM_RULE,
            datetime(2025, 8, 13, 4, 0, 0, tzinfo=UTC),
        ),
        (
            "example.ru.txt",
            "example.ru",
            RU_RULE,
            datetime(2026, 4, 12, 21, 0, 0, tzinfo=UTC),
        ),
        (
            "example.co.uk.txt",
            "example.co.uk",
            CO_UK_RULE,
            # 14-Aug-2026 00:00 Europe/London = 13-Aug-2026 23:00 UTC (BST in August).
            datetime(2026, 8, 13, 23, 0, 0, tzinfo=UTC),
        ),
        (
            "example.app.txt",
            "example.app",
            APP_RULE,
            datetime(2027, 5, 8, 0, 0, 0, tzinfo=UTC),
        ),
        (
            "example.io.txt",
            "example.io",
            IO_RULE,
            datetime(2025, 8, 14, 23, 59, 59, tzinfo=UTC),
        ),
    ],
)
async def test_fixture_parses_to_expected(
    fixture: str, domain: str, rule: ParseRule, expected: datetime
) -> None:
    parser = RegexWhoisParser()
    out = await parser.parse(_read(fixture), DomainName(domain), [rule])
    assert out == expected
    assert out.tzinfo is not None


async def test_no_matching_rule_for_tld_raises() -> None:
    parser = RegexWhoisParser()
    raw = _read("example.com.txt")
    with pytest.raises(NoMatchingRuleError) as excinfo:
        await parser.parse(raw, DomainName("example.com"), [RU_RULE])
    assert "com" in str(excinfo.value)


async def test_rule_for_tld_but_regex_misses_raises() -> None:
    parser = RegexWhoisParser()
    raw = _read("example.com.txt")
    rule = ParseRule(
        tld="com",
        expires_regex=RegexPattern(r"DEFINITELY-NOT-PRESENT:\s*(\S+)"),
        date_format=DateFormat.ISO_8601,
    )
    with pytest.raises(NoMatchingRuleError):
        await parser.parse(raw, DomainName("example.com"), [rule])


async def test_match_but_unparseable_date_raises_parse_error() -> None:
    parser = RegexWhoisParser()
    raw = "Domain: x.com\nRegistry Expiry Date: definitely-not-a-date\n"
    with pytest.raises(ParseError):
        await parser.parse(raw, DomainName("example.com"), [COM_RULE])


async def test_first_match_wins_when_multiple_rules_match() -> None:
    parser = RegexWhoisParser()
    raw = "Registry Expiry Date: 2030-01-01T00:00:00Z\n"
    other = ParseRule(
        tld="com",
        expires_regex=RegexPattern(r"Registry Expiry Date:\s*(\S+)"),
        date_format=DateFormat.ISO_8601,
    )
    out = await parser.parse(raw, DomainName("example.com"), [COM_RULE, other])
    assert out == datetime(2030, 1, 1, tzinfo=UTC)


async def test_parse_error_falls_through_to_later_rule() -> None:
    """If an earlier rule matches but parses badly, try a later rule."""
    parser = RegexWhoisParser()
    raw = "BadField: garbage\nRegistry Expiry Date: 2030-01-01T00:00:00Z\n"
    bad_rule = ParseRule(
        tld="com",
        expires_regex=RegexPattern(r"BadField:\s*(\S+)"),
        date_format=DateFormat.ISO_8601,
    )
    out = await parser.parse(raw, DomainName("example.com"), [bad_rule, COM_RULE])
    assert out == datetime(2030, 1, 1, tzinfo=UTC)


async def test_only_bad_rule_raises_last_parse_error() -> None:
    parser = RegexWhoisParser()
    raw = "Registry Expiry Date: not-a-date\n"
    with pytest.raises(ParseError):
        await parser.parse(raw, DomainName("example.com"), [COM_RULE])


async def test_yyyy_mm_dd_format() -> None:
    parser = RegexWhoisParser()
    rule = ParseRule(
        tld="com",
        expires_regex=RegexPattern(r"expiry:\s*(\d{4}-\d{2}-\d{2})"),
        date_format=DateFormat.YYYY_MM_DD,
    )
    out = await parser.parse("expiry: 2027-05-09\n", DomainName("example.com"), [rule])
    assert out == datetime(2027, 5, 9, tzinfo=UTC)


async def test_epoch_seconds_format() -> None:
    parser = RegexWhoisParser()
    rule = ParseRule(
        tld="com",
        expires_regex=RegexPattern(r"epoch:\s*(\d+)"),
        date_format=DateFormat.EPOCH_SECONDS,
    )
    out = await parser.parse("epoch: 1800000000\n", DomainName("example.com"), [rule])
    assert out == datetime.fromtimestamp(1800000000, tz=UTC)


async def test_custom_strptime_format() -> None:
    parser = RegexWhoisParser()
    rule = ParseRule(
        tld="com",
        expires_regex=RegexPattern(r"exp=([0-9/]+)"),
        date_format=DateFormat.CUSTOM,
        strptime_format="%Y/%m/%d",
        timezone="UTC",
    )
    out = await parser.parse("exp=2030/12/31\n", DomainName("example.com"), [rule])
    assert out == datetime(2030, 12, 31, tzinfo=UTC)


async def test_unknown_timezone_raises_parse_error() -> None:
    parser = RegexWhoisParser()
    rule = ParseRule(
        tld="com",
        expires_regex=RegexPattern(r"exp:\s*(\S+)"),
        date_format=DateFormat.YYYY_MM_DD,
        timezone="Mars/Olympus",
    )
    with pytest.raises(ParseError):
        await parser.parse("exp: 2030-01-01\n", DomainName("example.com"), [rule])


async def test_empty_rules_raises_no_matching_rule() -> None:
    parser = RegexWhoisParser()
    with pytest.raises(NoMatchingRuleError):
        await parser.parse("anything", DomainName("example.com"), [])
