"""RevalidationService: due-detection, revalidation, demotion."""

from __future__ import annotations

from datetime import UTC, datetime

from domain_watcher.application.services.revalidation_service import RevalidationService
from domain_watcher.core.parsing.events import (
    WhoisRuleInvalidated,
    WhoisRuleRevalidated,
)
from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    LearnedRule,
    RegexPattern,
)
from domain_watcher.core.shared.errors import RuleValidationError
from domain_watcher.core.shared.time_provider import FixedClock
from domain_watcher.core.shared.value_objects import DomainName, Duration

from ._fakes import FakeLearnedRules, FakePublisher, FakeValidationPipeline

NOW = datetime(2026, 5, 9, tzinfo=UTC)


def _learned(
    *,
    rid: int = 1,
    last_revalidated_at: datetime | None,
    sample_domain: str = "iana.org",
) -> LearnedRule:
    return LearnedRule(
        id=rid,
        tld="org",
        expires_regex=RegexPattern(r"Registry Expiry Date:\s*(\S+)"),
        date_format=DateFormat.ISO_8601,
        timezone="UTC",
        strptime_format=None,
        auto_learned=True,
        disabled=False,
        suggester_id="fake",
        pipeline_version=1,
        sample_whois_sha256="0" * 64,
        sample_domain=DomainName(sample_domain),
        created_at=NOW,
        last_revalidated_at=last_revalidated_at,
        revalidation_count=0,
    )


async def test_due_rule_revalidated() -> None:
    learned = FakeLearnedRules()
    rule = _learned(last_revalidated_at=None)
    learned.all_rules = [rule]
    pipeline = FakeValidationPipeline()
    publisher = FakePublisher()

    async def fetch(_: DomainName) -> str:
        return "Registry Expiry Date: 2030-01-01T00:00:00Z"

    svc = RevalidationService(
        learned_rules=learned,
        validation_pipeline=pipeline,
        fetch_whois=fetch,
        publisher=publisher,
        clock=FixedClock(NOW),
        revalidate_after=Duration.days(30),
    )
    await svc.run_once()
    assert len(learned.revalidated) == 1
    assert sum(isinstance(e, WhoisRuleRevalidated) for e in publisher.events) == 1


async def test_validation_failure_disables_rule() -> None:
    learned = FakeLearnedRules()
    rule = _learned(last_revalidated_at=None)
    learned.all_rules = [rule]
    pipeline = FakeValidationPipeline(behavior=RuleValidationError("regex stale"))
    publisher = FakePublisher()

    async def fetch(_: DomainName) -> str:
        return "different format"

    svc = RevalidationService(
        learned_rules=learned,
        validation_pipeline=pipeline,
        fetch_whois=fetch,
        publisher=publisher,
        clock=FixedClock(NOW),
        revalidate_after=Duration.days(30),
    )
    await svc.run_once()
    assert len(learned.disabled) == 1
    assert sum(isinstance(e, WhoisRuleInvalidated) for e in publisher.events) == 1


async def test_not_due_rule_skipped() -> None:
    learned = FakeLearnedRules()
    rule = _learned(last_revalidated_at=NOW)  # just revalidated
    learned.all_rules = [rule]
    pipeline = FakeValidationPipeline()
    publisher = FakePublisher()

    async def fetch(_: DomainName) -> str:
        raise AssertionError("should not be called")

    svc = RevalidationService(
        learned_rules=learned,
        validation_pipeline=pipeline,
        fetch_whois=fetch,
        publisher=publisher,
        clock=FixedClock(NOW),
        revalidate_after=Duration.days(30),
    )
    await svc.run_once()
    assert learned.revalidated == []
    assert learned.disabled == []
