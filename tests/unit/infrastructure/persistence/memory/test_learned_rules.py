"""MemoryLearnedRulesRepo: add, dedup, disable, revalidate."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.persistence.memory import MemoryLearnedRulesRepo

NOW = datetime(2026, 5, 9, tzinfo=UTC)
RULE = ParseRule(
    tld="com",
    expires_regex=RegexPattern(r"Registry Expiry Date:\s*(\S+)"),
    date_format=DateFormat.ISO_8601,
)
SAMPLE = DomainName("example.com")


async def test_add_assigns_id_and_lists_for_tld() -> None:
    repo = MemoryLearnedRulesRepo()
    rid = await repo.add(
        RULE,
        sample_sha256="0" * 64,
        sample_domain=SAMPLE,
        suggester_id="fake",
        pipeline_version=1,
    )
    assert rid == 1
    rules = await repo.for_tld("com")
    assert len(rules) == 1


async def test_duplicate_rejected() -> None:
    repo = MemoryLearnedRulesRepo()
    await repo.add(
        RULE,
        sample_sha256="0" * 64,
        sample_domain=SAMPLE,
        suggester_id="fake",
        pipeline_version=1,
    )
    with pytest.raises(ValueError):
        await repo.add(
            RULE,
            sample_sha256="0" * 64,
            sample_domain=SAMPLE,
            suggester_id="fake",
            pipeline_version=1,
        )


async def test_disabled_rule_excluded_from_for_tld() -> None:
    repo = MemoryLearnedRulesRepo()
    rid = await repo.add(
        RULE,
        sample_sha256="0" * 64,
        sample_domain=SAMPLE,
        suggester_id="fake",
        pipeline_version=1,
    )
    await repo.disable(rid, "stale")
    rules = await repo.for_tld("com")
    assert rules == ()
    all_visible = await repo.list_all()
    assert all_visible == ()
    all_with_disabled = await repo.list_all(include_disabled=True)
    assert len(all_with_disabled) == 1


async def test_mark_revalidated_increments_count() -> None:
    repo = MemoryLearnedRulesRepo()
    rid = await repo.add(
        RULE,
        sample_sha256="0" * 64,
        sample_domain=SAMPLE,
        suggester_id="fake",
        pipeline_version=1,
    )
    await repo.mark_revalidated(rid, NOW)
    out = await repo.list_all()
    assert out[0].last_revalidated_at == NOW
    assert out[0].revalidation_count == 1


async def test_disable_unknown_raises() -> None:
    repo = MemoryLearnedRulesRepo()
    with pytest.raises(KeyError):
        await repo.disable(999, "x")
