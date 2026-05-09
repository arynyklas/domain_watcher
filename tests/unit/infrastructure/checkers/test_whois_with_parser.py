"""WhoisCheckerWithParser composite: success, parse_error, no-match passthrough."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.shared.errors import NoMatchingRuleError
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.checkers._whois_with_parser import (
    WhoisCheckerWithParser,
)
from domain_watcher.infrastructure.checkers.whois import _WhoisFetcher

if TYPE_CHECKING:
    import pytest


class _Record:
    def __init__(self, text: str) -> None:
        self.text = text


async def test_composite_id_is_whois() -> None:
    assert WhoisCheckerWithParser.id == "whois"


async def test_parse_success(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = "Registry Expiry Date: 2027-01-01T00:00:00Z\n"
    expected = datetime(2027, 1, 1, tzinfo=UTC)

    def fake_whois(domain: str) -> Any:
        return _Record(raw)

    monkeypatch.setattr("domain_watcher.infrastructure.checkers.whois.whois.whois", fake_whois)

    async def parse(text: str, domain: DomainName) -> datetime:
        assert text == raw
        return expected

    composite = WhoisCheckerWithParser(fetcher=_WhoisFetcher(), parse=parse)
    result = await composite.check(DomainName("example.com"))
    assert result.outcome is CheckOutcome.OK
    assert result.expires_at == expected
    assert result.source == "whois"


async def test_parse_error_becomes_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = "weird format"

    def fake_whois(domain: str) -> Any:
        return _Record(raw)

    monkeypatch.setattr("domain_watcher.infrastructure.checkers.whois.whois.whois", fake_whois)

    async def parse(text: str, domain: DomainName) -> datetime:
        raise NoMatchingRuleError("no rule")

    composite = WhoisCheckerWithParser(fetcher=_WhoisFetcher(), parse=parse)
    result = await composite.check(DomainName("example.com"))
    assert result.outcome is CheckOutcome.PERMANENT_ERROR
    assert "parse_error" in (result.error or "")


async def test_no_match_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = "No match for ZZ.invalid"

    def fake_whois(domain: str) -> Any:
        return _Record(raw)

    monkeypatch.setattr("domain_watcher.infrastructure.checkers.whois.whois.whois", fake_whois)

    async def parse(text: str, domain: DomainName) -> datetime:  # pragma: no cover
        raise AssertionError("parser should not run on no-match")

    composite = WhoisCheckerWithParser(fetcher=_WhoisFetcher(), parse=parse)
    result = await composite.check(DomainName("zz.invalid"))
    assert result.outcome is CheckOutcome.PERMANENT_ERROR
    assert result.raw == raw
