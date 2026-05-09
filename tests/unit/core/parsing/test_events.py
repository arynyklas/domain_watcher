from __future__ import annotations

from datetime import UTC, datetime

from domain_watcher.core.parsing.events import (
    ParseFailed,
    WhoisRuleInvalidated,
    WhoisRuleLearned,
    WhoisRuleRevalidated,
)
from domain_watcher.core.shared.events import DomainEvent
from domain_watcher.core.shared.value_objects import DomainName


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def test_whois_rule_learned_is_standard() -> None:
    e = WhoisRuleLearned(
        occurred_at=_now(),
        correlation_id="ulid",
        rule_id=1,
        tld="xyz",
        sample_domain=DomainName("example.xyz"),
        suggester_id="litellm:ollama/gemma3",
    )
    assert isinstance(e, DomainEvent)
    assert e.criticality == "standard"


def test_whois_rule_revalidated() -> None:
    e = WhoisRuleRevalidated(
        occurred_at=_now(),
        correlation_id="ulid",
        rule_id=1,
        tld="xyz",
    )
    assert isinstance(e, DomainEvent)
    assert e.criticality == "standard"


def test_whois_rule_invalidated_is_critical() -> None:
    e = WhoisRuleInvalidated(
        occurred_at=_now(),
        correlation_id="ulid",
        rule_id=1,
        tld="xyz",
        reason="re-validation failed gate 3",
    )
    assert e.criticality == "critical"


def test_parse_failed_is_critical() -> None:
    e = ParseFailed(
        occurred_at=_now(),
        correlation_id="ulid",
        domain=DomainName("example.com"),
        reason="no matching rule",
        fallback_attempted=False,
    )
    assert e.criticality == "critical"
