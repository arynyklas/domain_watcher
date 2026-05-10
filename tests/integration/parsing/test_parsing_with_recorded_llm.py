"""Recorded-fixture LLM run.

Default CI runs only this test from the integration suite — it is fully
hermetic: ``litellm.acompletion`` is patched to return a JSON blob from
``tests/fixtures/llm/``. The live-LLM equivalent lives in
``test_parsing_with_real_llm.py`` and is gated behind ``LLM_INTEGRATION=1``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from domain_watcher.application.services.parsing_service import ParsingService
from domain_watcher.core.parsing.events import WhoisRuleLearned
from domain_watcher.core.shared.errors import SuggestionError
from domain_watcher.core.shared.time_provider import FixedClock
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.parsers.llm_suggester import LiteLLMRuleSuggester
from domain_watcher.infrastructure.parsers.regex import RegexWhoisParser
from domain_watcher.infrastructure.parsers.validation_pipeline import ValidationPipeline
from domain_watcher.infrastructure.persistence.memory.learned_rules import (
    MemoryLearnedRulesRepo,
)

if TYPE_CHECKING:
    from domain_watcher.core.shared.events import DomainEvent

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_LLM = REPO_ROOT / "tests" / "fixtures" / "llm"
FIXTURES_WHOIS = REPO_ROOT / "tests" / "fixtures" / "whois"
NOW = datetime(2024, 8, 15, tzinfo=UTC)


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_LLM / name).read_text(encoding="utf-8"))


class _RecordingPublisher:
    """Inline FakePublisher to avoid cross-test-package imports."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)


class _StubFetcher:
    """Cross-check fetcher that returns the same WHOIS body so gate 5 passes."""

    def __init__(self, raw: str) -> None:
        self._raw = raw
        self.calls: list[str] = []

    async def fetch_raw(self, domain: DomainName) -> str:
        self.calls.append(domain.value)
        return self._raw


async def test_recorded_llm_happy_path_persists_and_publishes() -> None:
    raw_whois = (FIXTURES_WHOIS / "example.com.txt").read_text(encoding="utf-8")
    fixture = _load_fixture("unknown_tld_ok.json")

    suggester = LiteLLMRuleSuggester(
        model="ollama/gemma3", api_base="http://localhost:11434"
    )
    pipeline = ValidationPipeline(
        cross_check_fetcher=_StubFetcher(raw_whois),
        clock=FixedClock(NOW),
        known_good={"com": ("iana.org",)},
    )
    repo = MemoryLearnedRulesRepo()
    parser = RegexWhoisParser()
    publisher = _RecordingPublisher()
    service = ParsingService(
        parser=parser,
        learned_rules=repo,
        publisher=publisher,
        clock=FixedClock(NOW),
        suggester=suggester,
        validation_pipeline=pipeline,
    )

    with patch("litellm.acompletion", new=AsyncMock(return_value=fixture)):
        out = await service.parse(raw_whois, DomainName("example.com"))

    # The recorded rule extracts ``Registry Expiry Date`` → 2025-08-13T04:00:00Z.
    assert out == datetime(2025, 8, 13, 4, 0, 0, tzinfo=UTC)
    rules = await repo.list_all()
    assert len(rules) == 1
    assert rules[0].tld == "com"
    assert sum(isinstance(e, WhoisRuleLearned) for e in publisher.events) == 1


async def test_recorded_llm_bad_json_is_rejected() -> None:
    raw_whois = (FIXTURES_WHOIS / "example.com.txt").read_text(encoding="utf-8")
    fixture = _load_fixture("bad_json_response.json")

    suggester = LiteLLMRuleSuggester(model="ollama/gemma3")
    pipeline = ValidationPipeline(
        cross_check_fetcher=_StubFetcher(raw_whois),
        clock=FixedClock(NOW),
        known_good={"com": ("iana.org",)},
    )
    repo = MemoryLearnedRulesRepo()
    service = ParsingService(
        parser=RegexWhoisParser(),
        learned_rules=repo,
        publisher=_RecordingPublisher(),
        clock=FixedClock(NOW),
        suggester=suggester,
        validation_pipeline=pipeline,
    )

    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=fixture)),
        pytest.raises(SuggestionError),
    ):
        await service.parse(raw_whois, DomainName("example.com"))
    # No rule should have been persisted.
    assert (await repo.list_all()) == ()


async def test_recorded_llm_missing_capture_group_is_rejected() -> None:
    raw_whois = (FIXTURES_WHOIS / "example.com.txt").read_text(encoding="utf-8")
    fixture = _load_fixture("missing_capture_group.json")

    suggester = LiteLLMRuleSuggester(model="ollama/gemma3")
    pipeline = ValidationPipeline(
        cross_check_fetcher=_StubFetcher(raw_whois),
        clock=FixedClock(NOW),
        known_good={"com": ("iana.org",)},
    )
    repo = MemoryLearnedRulesRepo()
    service = ParsingService(
        parser=RegexWhoisParser(),
        learned_rules=repo,
        publisher=_RecordingPublisher(),
        clock=FixedClock(NOW),
        suggester=suggester,
        validation_pipeline=pipeline,
    )

    with (
        patch("litellm.acompletion", new=AsyncMock(return_value=fixture)),
        pytest.raises(SuggestionError),
    ):
        await service.parse(raw_whois, DomainName("example.com"))
    assert (await repo.list_all()) == ()
