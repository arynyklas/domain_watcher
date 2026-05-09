"""_WhoisFetcher: text extraction, no-match, timeout, generic exception."""

from __future__ import annotations

from typing import Any

import pytest

from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.checkers.whois import _WhoisFetcher


class _FakeRecord:
    def __init__(self, text: str) -> None:
        self.text = text


@pytest.fixture
def fetcher() -> _WhoisFetcher:
    return _WhoisFetcher(timeout=2.0)


async def test_returns_raw_text(monkeypatch: pytest.MonkeyPatch, fetcher: _WhoisFetcher) -> None:
    raw_text = "Domain Name: EXAMPLE.COM\nRegistry Expiry Date: 2027-01-01T00:00:00Z\n"

    def fake_whois(domain: str) -> Any:
        return _FakeRecord(raw_text)

    monkeypatch.setattr("domain_watcher.infrastructure.checkers.whois.whois.whois", fake_whois)
    result = await fetcher.fetch(DomainName("example.com"))
    # Fetcher does not parse; raw is set, outcome is non-OK by design.
    assert result.outcome is CheckOutcome.TRANSIENT_ERROR
    assert result.raw == raw_text


async def test_no_match_permanent(monkeypatch: pytest.MonkeyPatch, fetcher: _WhoisFetcher) -> None:
    raw = "No match for ZZ.invalid"

    def fake_whois(domain: str) -> Any:
        return _FakeRecord(raw)

    monkeypatch.setattr("domain_watcher.infrastructure.checkers.whois.whois.whois", fake_whois)
    result = await fetcher.fetch(DomainName("zz.invalid"))
    assert result.outcome is CheckOutcome.PERMANENT_ERROR
    assert "no match" in (result.error or "")


async def test_unknown_exception_transient(
    monkeypatch: pytest.MonkeyPatch, fetcher: _WhoisFetcher
) -> None:
    def boom(domain: str) -> Any:
        raise RuntimeError("registry hiccup")

    monkeypatch.setattr("domain_watcher.infrastructure.checkers.whois.whois.whois", boom)
    result = await fetcher.fetch(DomainName("example.com"))
    assert result.outcome is CheckOutcome.TRANSIENT_ERROR
    assert "registry hiccup" in (result.error or "")


async def test_timeout_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    def slow(domain: str) -> Any:
        time.sleep(2)
        return _FakeRecord("never")

    monkeypatch.setattr("domain_watcher.infrastructure.checkers.whois.whois.whois", slow)
    fetcher = _WhoisFetcher(timeout=0.05)
    result = await fetcher.fetch(DomainName("example.com"))
    assert result.outcome is CheckOutcome.TRANSIENT_ERROR
    assert "timeout" in (result.error or "")


async def test_empty_payload_transient(
    monkeypatch: pytest.MonkeyPatch, fetcher: _WhoisFetcher
) -> None:
    def empty(domain: str) -> Any:
        return _FakeRecord("")

    monkeypatch.setattr("domain_watcher.infrastructure.checkers.whois.whois.whois", empty)
    result = await fetcher.fetch(DomainName("example.com"))
    assert result.outcome is CheckOutcome.TRANSIENT_ERROR
