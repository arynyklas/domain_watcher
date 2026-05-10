"""``ValidationPipeline`` — gate-by-gate behavior tests (ADR 0006 §4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.errors import (
    PermanentCheckError,
    RuleValidationError,
    SuggestionError,
    TransientCheckError,
)
from domain_watcher.core.shared.time_provider import FixedClock
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.parsers._metrics import pipeline_gate5_skipped_total
from domain_watcher.infrastructure.parsers.validation_pipeline import ValidationPipeline

NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
COM_RAW = (
    "Domain: example.com\n"
    "Creation Date: 2010-08-13T04:00:00Z\n"
    "Registry Expiry Date: 2030-08-13T04:00:00Z\n"
)
KG_RAW = "Domain: iana.org\nRegistry Expiry Date: 2031-09-09T00:00:00Z\n"
GOOD_RULE = ParseRule(
    tld="com",
    expires_regex=RegexPattern(r"Registry Expiry Date:\s*(\S+)"),
    date_format=DateFormat.ISO_8601,
)


@dataclass
class FakeFetcher:
    responses: dict[str, str] = field(default_factory=dict)
    transient: dict[str, BaseException] = field(default_factory=dict)
    permanent: dict[str, BaseException] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def fetch_raw(self, domain: DomainName) -> str:
        self.calls.append(domain.value)
        if domain.value in self.transient:
            raise self.transient[domain.value]
        if domain.value in self.permanent:
            raise self.permanent[domain.value]
        if domain.value in self.responses:
            return self.responses[domain.value]
        raise AssertionError(f"FakeFetcher: no setup for {domain.value}")


def _pipeline(
    fetcher: FakeFetcher,
    *,
    known_good: dict[str, tuple[str, ...]] | None = None,
    now: datetime = NOW,
) -> ValidationPipeline:
    kg = known_good if known_good is not None else {"com": ("iana.org",)}
    return ValidationPipeline(
        cross_check_fetcher=fetcher,
        clock=FixedClock(now),
        known_good=kg,
    )


@pytest.fixture(autouse=True)
def _reset_metric() -> None:
    pipeline_gate5_skipped_total.reset()


async def test_happy_path_passes() -> None:
    fetcher = FakeFetcher(responses={"iana.org": KG_RAW})
    p = _pipeline(fetcher)
    await p.validate(GOOD_RULE, raw_whois=COM_RAW, domain=DomainName("example.com"))


async def test_gate2_no_match_in_raw_rejects() -> None:
    fetcher = FakeFetcher(responses={"iana.org": KG_RAW})
    p = _pipeline(fetcher)
    with pytest.raises(RuleValidationError, match="gate2"):
        await p.validate(
            GOOD_RULE,
            raw_whois="Domain: x.com\n",  # no Registry Expiry Date line
            domain=DomainName("example.com"),
        )


async def test_gate3_unparseable_capture_rejects() -> None:
    fetcher = FakeFetcher(responses={"iana.org": KG_RAW})
    p = _pipeline(fetcher)
    raw = "Registry Expiry Date: not-a-date\n"
    with pytest.raises(RuleValidationError, match="gate3"):
        await p.validate(GOOD_RULE, raw_whois=raw, domain=DomainName("example.com"))


async def test_gate4_past_date_rejects() -> None:
    fetcher = FakeFetcher(responses={"iana.org": KG_RAW})
    p = _pipeline(fetcher)
    raw = "Registry Expiry Date: 2020-01-01T00:00:00Z\n"
    with pytest.raises(RuleValidationError, match="not in the future"):
        await p.validate(GOOD_RULE, raw_whois=raw, domain=DomainName("example.com"))


async def test_gate4_too_far_future_rejects() -> None:
    fetcher = FakeFetcher(responses={"iana.org": KG_RAW})
    p = _pipeline(fetcher, now=NOW)
    raw = "Registry Expiry Date: 2999-01-01T00:00:00Z\n"
    with pytest.raises(RuleValidationError, match="more than"):
        await p.validate(GOOD_RULE, raw_whois=raw, domain=DomainName("example.com"))


async def test_gate4_registration_date_match_rejects() -> None:
    """If the regex captures the registration date instead of expiration."""
    fetcher = FakeFetcher(responses={"iana.org": KG_RAW})
    p = _pipeline(fetcher)
    # Both lines yield the same date — looks like it picked Created/Registered.
    raw = "Created: 2030-08-13T04:00:00Z\nRegistry Expiry Date: 2030-08-13T04:00:00Z\n"
    with pytest.raises(RuleValidationError, match="registration date"):
        await p.validate(GOOD_RULE, raw_whois=raw, domain=DomainName("example.com"))


async def test_gate5_overfit_rejects() -> None:
    """Rule matches the suggesting WHOIS but not the known-good's WHOIS."""
    kg_no_match = "Domain: iana.org\nNoExpiryHere: 2031-01-01\n"
    fetcher = FakeFetcher(responses={"iana.org": kg_no_match})
    p = _pipeline(fetcher)
    with pytest.raises(RuleValidationError, match="known-good"):
        await p.validate(GOOD_RULE, raw_whois=COM_RAW, domain=DomainName("example.com"))


async def test_gate5_no_known_good_skips_with_metric() -> None:
    fetcher = FakeFetcher()
    p = _pipeline(fetcher, known_good={})
    await p.validate(GOOD_RULE, raw_whois=COM_RAW, domain=DomainName("example.com"))
    assert pipeline_gate5_skipped_total.value("no_known_good") == 1
    assert fetcher.calls == []


async def test_gate5_skips_when_known_good_is_self() -> None:
    """Don't cross-check a rule against the very domain that produced it."""
    fetcher = FakeFetcher()
    p = _pipeline(fetcher, known_good={"com": ("example.com",)})
    await p.validate(GOOD_RULE, raw_whois=COM_RAW, domain=DomainName("example.com"))
    assert pipeline_gate5_skipped_total.value("no_known_good") == 1
    assert fetcher.calls == []


async def test_gate5_transient_raises_suggestion_error_not_validation_error() -> None:
    fetcher = FakeFetcher(transient={"iana.org": TransientCheckError("flaky registry")})
    p = _pipeline(fetcher)
    with pytest.raises(SuggestionError) as exc_info:
        await p.validate(GOOD_RULE, raw_whois=COM_RAW, domain=DomainName("example.com"))
    assert exc_info.value.transient is True
    assert pipeline_gate5_skipped_total.value("cross_check_unavailable") == 1


async def test_gate5_permanent_skips_with_metric() -> None:
    fetcher = FakeFetcher(permanent={"iana.org": PermanentCheckError("no such domain")})
    p = _pipeline(fetcher)
    await p.validate(GOOD_RULE, raw_whois=COM_RAW, domain=DomainName("example.com"))
    assert pipeline_gate5_skipped_total.value("cross_check_unavailable") == 1


async def test_gate5_cache_avoids_second_fetch_within_revalidate_after() -> None:
    fetcher = FakeFetcher(responses={"iana.org": KG_RAW})
    p = _pipeline(fetcher)
    # Two consecutive validations within the cache window → one fetch.
    await p.validate(GOOD_RULE, raw_whois=COM_RAW, domain=DomainName("example.com"))
    await p.validate(GOOD_RULE, raw_whois=COM_RAW, domain=DomainName("example.com"))
    assert fetcher.calls == ["iana.org"]


async def test_gate5_cache_expires_past_revalidate_after() -> None:
    fetcher = FakeFetcher(responses={"iana.org": KG_RAW})
    clock = FixedClock(NOW)
    p = ValidationPipeline(
        cross_check_fetcher=fetcher,
        clock=clock,
        revalidate_after_seconds=10,
        known_good={"com": ("iana.org",)},
    )
    await p.validate(GOOD_RULE, raw_whois=COM_RAW, domain=DomainName("example.com"))
    # Advance well past TTL.
    from datetime import timedelta

    clock.advance(timedelta(seconds=20))
    await p.validate(GOOD_RULE, raw_whois=COM_RAW, domain=DomainName("example.com"))
    assert fetcher.calls == ["iana.org", "iana.org"]


async def test_gate6_sentinel_date_rejects() -> None:
    fetcher = FakeFetcher(responses={"iana.org": KG_RAW})
    # We'll use an epoch-formatted rule that yields exactly 1970-01-01.
    rule = ParseRule(
        tld="com",
        expires_regex=RegexPattern(r"epoch:\s*(\d+)"),
        date_format=DateFormat.EPOCH_SECONDS,
    )
    raw = "epoch: 0\nRegistry Expiry Date: anything\n"
    p = _pipeline(fetcher)
    # epoch=0 → 1970-01-01 → fails range first ("not in future"), good.
    with pytest.raises(RuleValidationError):
        await p.validate(rule, raw_whois=raw, domain=DomainName("example.com"))

    # Now hand-craft a rule that captures sentinel-like 9999-12-31.
    # Use future-clock so the range gate passes, then gate-6 catches it.
    rule_far = ParseRule(
        tld="com",
        expires_regex=RegexPattern(r"sentinel:\s*(\S+)"),
        date_format=DateFormat.ISO_8601,
    )
    raw2 = (
        "sentinel: 9999-12-31T00:00:00Z\nRegistry Expiry Date: 2030-08-13T04:00:00Z\n"
    )
    fetcher.responses["iana.org"] = "sentinel: 9999-12-31T00:00:00Z\n"
    far_clock_pipeline = ValidationPipeline(
        cross_check_fetcher=fetcher,
        clock=FixedClock(NOW),
        max_age_years=10_000,
        known_good={"com": ("iana.org",)},
    )
    with pytest.raises(RuleValidationError, match="gate6"):
        await far_clock_pipeline.validate(
            rule_far, raw_whois=raw2, domain=DomainName("example.com")
        )


async def test_pipeline_version_class_var_is_one() -> None:
    """Bumping pipeline_version is a deliberate event — pin it explicitly."""
    assert ValidationPipeline.pipeline_version == 1
