"""ParsingService orchestration: static, learned, LLM fallback paths."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain_watcher.application.services.parsing_service import ParsingService
from domain_watcher.core.parsing.events import ParseFailed, WhoisRuleLearned
from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.errors import (
    NoMatchingRuleError,
    RuleValidationError,
    SuggestionError,
)
from domain_watcher.core.shared.time_provider import FixedClock
from domain_watcher.core.shared.value_objects import DomainName

from ._fakes import (
    FakeLearnedRules,
    FakeLimiter,
    FakePublisher,
    FakeSuggester,
    FakeValidationPipeline,
    FakeWhoisParser,
)

NOW = datetime(2026, 5, 9, tzinfo=UTC)
EXPIRES = datetime(2027, 1, 1, tzinfo=UTC)
RAW = "Domain: example.com\nRegistry Expiry Date: 2027-01-01T00:00:00Z\n"

STATIC_RULE = ParseRule(
    tld="com",
    expires_regex=RegexPattern(r"Registry Expiry Date:\s*(\S+)"),
    date_format=DateFormat.ISO_8601,
)
LEARNED_RULE = ParseRule(
    tld="com",
    expires_regex=RegexPattern(r"paid-till:\s*(\S+)"),
    date_format=DateFormat.ISO_8601,
)
SUGGESTED_RULE = ParseRule(
    tld="com",
    expires_regex=RegexPattern(r"Expires:\s*(\S+)"),
    date_format=DateFormat.ISO_8601,
)


def _build_service(
    *,
    parser: FakeWhoisParser,
    learned: FakeLearnedRules,
    publisher: FakePublisher,
    static_rules: tuple[ParseRule, ...] = (),
    suggester: FakeSuggester | None = None,
    pipeline: FakeValidationPipeline | None = None,
    host_limiter: FakeLimiter | None = None,
    tld_limiter: FakeLimiter | None = None,
) -> ParsingService:
    return ParsingService(
        parser=parser,
        learned_rules=learned,
        publisher=publisher,
        clock=FixedClock(NOW),
        static_rules=static_rules,
        suggester=suggester,
        validation_pipeline=pipeline,
        host_limiter=host_limiter,
        tld_limiter=tld_limiter,
    )


async def test_static_rule_path() -> None:
    parser = FakeWhoisParser()
    parser.map_raw(RAW, EXPIRES)
    learned = FakeLearnedRules()
    publisher = FakePublisher()
    svc = _build_service(
        parser=parser, learned=learned, publisher=publisher, static_rules=(STATIC_RULE,)
    )
    out = await svc.parse(RAW, DomainName("example.com"))
    assert out == EXPIRES
    assert publisher.events == []


async def test_learned_rule_path() -> None:
    parser = FakeWhoisParser()
    learned = FakeLearnedRules()
    learned.by_tld["com"] = [LEARNED_RULE]
    parser.map_raw(RAW, EXPIRES)  # parser keyed only on raw text
    publisher = FakePublisher()
    svc = _build_service(parser=parser, learned=learned, publisher=publisher)
    out = await svc.parse(RAW, DomainName("example.com"))
    assert out == EXPIRES


async def test_fallback_disabled_emits_parse_failed() -> None:
    parser = FakeWhoisParser()  # no mappings → always NoMatchingRuleError
    learned = FakeLearnedRules()
    publisher = FakePublisher()
    svc = _build_service(parser=parser, learned=learned, publisher=publisher)
    with pytest.raises(NoMatchingRuleError):
        await svc.parse(RAW, DomainName("example.com"))
    assert sum(isinstance(e, ParseFailed) for e in publisher.events) == 1


async def test_llm_fallback_success_path() -> None:
    parser = FakeWhoisParser()
    parser.map_raw(RAW, EXPIRES)
    learned = FakeLearnedRules()
    publisher = FakePublisher()
    suggester = FakeSuggester(next_rule=SUGGESTED_RULE)
    pipeline = FakeValidationPipeline()
    svc = _build_service(
        parser=parser,
        learned=learned,
        publisher=publisher,
        suggester=suggester,
        pipeline=pipeline,
    )
    out = await svc.parse(RAW, DomainName("example.com"))
    assert out == EXPIRES
    assert len(learned.added) == 1
    assert sum(isinstance(e, WhoisRuleLearned) for e in publisher.events) == 1


async def test_llm_fallback_validation_rejects() -> None:
    parser = FakeWhoisParser()
    learned = FakeLearnedRules()
    publisher = FakePublisher()
    suggester = FakeSuggester(next_rule=SUGGESTED_RULE)
    pipeline = FakeValidationPipeline(behavior=RuleValidationError("range"))
    svc = _build_service(
        parser=parser,
        learned=learned,
        publisher=publisher,
        suggester=suggester,
        pipeline=pipeline,
    )
    with pytest.raises(RuleValidationError):
        await svc.parse(RAW, DomainName("example.com"))
    assert learned.added == []
    assert sum(isinstance(e, ParseFailed) for e in publisher.events) == 1


async def test_llm_fallback_validation_transient() -> None:
    parser = FakeWhoisParser()
    learned = FakeLearnedRules()
    publisher = FakePublisher()
    suggester = FakeSuggester(next_rule=SUGGESTED_RULE)
    pipeline = FakeValidationPipeline(behavior=SuggestionError("flake", transient=True))
    svc = _build_service(
        parser=parser,
        learned=learned,
        publisher=publisher,
        suggester=suggester,
        pipeline=pipeline,
    )
    with pytest.raises(SuggestionError):
        await svc.parse(RAW, DomainName("example.com"))
    assert learned.added == []


async def test_rate_limit_host_skips_suggester() -> None:
    parser = FakeWhoisParser()
    learned = FakeLearnedRules()
    publisher = FakePublisher()
    suggester = FakeSuggester(next_rule=SUGGESTED_RULE)
    pipeline = FakeValidationPipeline()
    host_limiter = FakeLimiter(budget=0)
    svc = _build_service(
        parser=parser,
        learned=learned,
        publisher=publisher,
        suggester=suggester,
        pipeline=pipeline,
        host_limiter=host_limiter,
    )
    with pytest.raises(SuggestionError):
        await svc.parse(RAW, DomainName("example.com"))
    assert suggester.calls == []
    assert sum(isinstance(e, ParseFailed) for e in publisher.events) == 1


async def test_rate_limit_tld_skips_suggester() -> None:
    parser = FakeWhoisParser()
    learned = FakeLearnedRules()
    publisher = FakePublisher()
    suggester = FakeSuggester(next_rule=SUGGESTED_RULE)
    pipeline = FakeValidationPipeline()
    tld_limiter = FakeLimiter(budget=0)
    svc = _build_service(
        parser=parser,
        learned=learned,
        publisher=publisher,
        suggester=suggester,
        pipeline=pipeline,
        tld_limiter=tld_limiter,
    )
    with pytest.raises(SuggestionError):
        await svc.parse(RAW, DomainName("example.com"))
    assert suggester.calls == []


async def test_suggestion_error_treated_as_parse_failed() -> None:
    parser = FakeWhoisParser()
    learned = FakeLearnedRules()
    publisher = FakePublisher()
    suggester = FakeSuggester(raises=SuggestionError("network"))
    pipeline = FakeValidationPipeline()
    svc = _build_service(
        parser=parser,
        learned=learned,
        publisher=publisher,
        suggester=suggester,
        pipeline=pipeline,
    )
    with pytest.raises(SuggestionError):
        await svc.parse(RAW, DomainName("example.com"))
    assert sum(isinstance(e, ParseFailed) for e in publisher.events) == 1
