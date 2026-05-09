from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from domain_watcher.core.parsing.ports import (
    LearnedRulesRepository,
    RuleSuggester,
    WhoisParser,
)
from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    LearnedRule,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.value_objects import DomainName

if TYPE_CHECKING:
    from collections.abc import Sequence


class _FakeParser:
    async def parse(
        self,
        raw: str,
        domain: DomainName,
        rules: Sequence[ParseRule],
    ) -> datetime:
        del raw, domain, rules
        return datetime(2027, 1, 1, tzinfo=UTC)


class _FakeSuggester:
    id = "fake-llm"

    async def suggest(self, raw_whois: str, domain: DomainName) -> ParseRule:
        del raw_whois, domain
        return ParseRule(
            tld="xyz",
            expires_regex=RegexPattern(r"Expiry:\s+(\S+)"),
            date_format=DateFormat.ISO_8601,
        )


class _FakeLearnedRepo:
    async def for_tld(self, tld: str) -> Sequence[ParseRule]:
        del tld
        return []

    async def add(
        self,
        rule: ParseRule,
        *,
        sample_sha256: str,
        sample_domain: DomainName,
        suggester_id: str,
        pipeline_version: int,
    ) -> None:
        del rule, sample_sha256, sample_domain, suggester_id, pipeline_version

    async def disable(self, rule_id: int, reason: str) -> None:
        del rule_id, reason

    async def list_all(self, *, include_disabled: bool = False) -> Sequence[LearnedRule]:
        del include_disabled
        return []

    async def mark_revalidated(self, rule_id: int, at: datetime) -> None:
        del rule_id, at


def test_parser_protocol() -> None:
    assert isinstance(_FakeParser(), WhoisParser)


def test_suggester_protocol() -> None:
    assert isinstance(_FakeSuggester(), RuleSuggester)


def test_learned_rules_repo_protocol() -> None:
    assert isinstance(_FakeLearnedRepo(), LearnedRulesRepository)


@pytest.mark.asyncio
async def test_parser_returns_datetime() -> None:
    p = _FakeParser()
    res = await p.parse("raw", DomainName("example.xyz"), [])
    assert res == datetime(2027, 1, 1, tzinfo=UTC)
