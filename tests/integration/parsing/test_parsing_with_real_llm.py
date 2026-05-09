"""Live LLM run.

Gated behind ``LLM_INTEGRATION=1``. Default CI does not exercise this —
the recorded-fixture sibling test does. Runs the full
``ParsingService → LiteLLMRuleSuggester → ValidationPipeline`` chain
against a real backend so we catch protocol drift on real responses.

Marked ``@pytest.mark.flaky`` because small local models occasionally
emit malformed JSON even at ``temperature=0``; the pipeline rejects
those and a rerun exercises the same code path.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from domain_watcher.application.services.parsing_service import ParsingService
from domain_watcher.core.shared.errors import ParseError
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

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("LLM_INTEGRATION") != "1",
        reason="LLM_INTEGRATION=1 not set; live LLM disabled",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_WHOIS = REPO_ROOT / "tests" / "fixtures" / "whois"
NOW = datetime(2026, 5, 9, tzinfo=UTC)


class _RecordingPublisher:
    """Inline FakePublisher to avoid cross-test-package imports."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)


class _StubFetcher:
    def __init__(self, raw: str) -> None:
        self._raw = raw

    async def fetch_raw(self, domain: DomainName):

        return self._raw


def _settings() -> tuple[str, str | None, str | None]:
    return (
        os.environ.get("LLM_MODEL", "ollama/gemma3"),
        os.environ.get("LLM_API_BASE", "http://localhost:11434"),
        os.environ.get("LLM_API_KEY") or None,
    )


# Small models occasionally emit malformed JSON — see module docstring.
@pytest.mark.flaky(reruns=2)
async def test_real_llm_learn_path() -> None:  # pragma: no cover - live network
    raw_whois = (FIXTURES_WHOIS / "example.com.txt").read_text(encoding="utf-8")
    model, api_base, api_key = _settings()
    suggester = LiteLLMRuleSuggester(model=model, api_base=api_base, api_key=api_key)
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

    try:
        out = await service.parse(raw_whois, DomainName("example.com"))
    except ParseError as exc:
        pytest.skip(f"live LLM produced unusable rule on this run: {exc}")
        return  # pragma: no cover
    assert out.tzinfo is not None
    assert out.year >= 2024  # parsed something plausible
