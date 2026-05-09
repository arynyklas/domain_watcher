from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    LearnedRule,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.value_objects import DomainName


class TestRegexPattern:
    def test_eager_compile(self) -> None:
        p = RegexPattern(r"paid-till:\s+(\S+)")
        assert p.compiled.pattern == r"paid-till:\s+(\S+)"

    def test_invalid_regex_rejected(self) -> None:
        with pytest.raises(ValueError, match="regex"):
            RegexPattern(r"unbalanced(group")

    def test_compiled_is_re_pattern(self) -> None:
        import re

        p = RegexPattern(r"foo: (\S+)")
        assert isinstance(p.compiled, re.Pattern)

    def test_equality_uses_raw(self) -> None:
        assert RegexPattern("(.*)") == RegexPattern("(.*)")
        assert RegexPattern("(.*)") != RegexPattern("(.+)")


class TestParseRule:
    def _r(
        self,
        *,
        tld: str = "ru",
        expires_regex: RegexPattern | None = None,
        date_format: DateFormat = DateFormat.ISO_8601,
        timezone: str = "UTC",
        strptime_format: str | None = None,
    ) -> ParseRule:
        return ParseRule(
            tld=tld,
            expires_regex=expires_regex or RegexPattern(r"paid-till:\s+(\S+)"),
            date_format=date_format,
            timezone=timezone,
            strptime_format=strptime_format,
        )

    def test_basic_construction(self) -> None:
        rule = self._r()
        assert rule.tld == "ru"
        assert rule.timezone == "UTC"
        assert rule.strptime_format is None

    def test_zero_capture_groups_rejected(self) -> None:
        with pytest.raises(ValueError, match="capture group"):
            self._r(expires_regex=RegexPattern(r"paid-till:\s+\S+"))

    def test_two_capture_groups_rejected(self) -> None:
        with pytest.raises(ValueError, match="capture group"):
            self._r(expires_regex=RegexPattern(r"(paid-till):\s+(\S+)"))

    def test_custom_format_requires_strptime(self) -> None:
        with pytest.raises(ValueError, match="strptime_format"):
            self._r(date_format=DateFormat.CUSTOM, strptime_format=None)

    def test_custom_format_with_strptime_ok(self) -> None:
        rule = self._r(date_format=DateFormat.CUSTOM, strptime_format="%Y-%m-%d")
        assert rule.strptime_format == "%Y-%m-%d"

    def test_non_custom_format_with_strptime_rejected(self) -> None:
        # Mixing CUSTOM-only fields with non-CUSTOM is a configuration smell.
        with pytest.raises(ValueError, match="strptime_format"):
            self._r(date_format=DateFormat.ISO_8601, strptime_format="%Y-%m-%d")

    def test_empty_tld_rejected(self) -> None:
        with pytest.raises(ValueError, match="tld"):
            self._r(tld="")


class TestLearnedRule:
    def test_basic_construction(self) -> None:
        r = LearnedRule(
            id=1,
            tld="xyz",
            expires_regex=RegexPattern(r"Expiry:\s+(\S+)"),
            date_format=DateFormat.ISO_8601,
            timezone="UTC",
            strptime_format=None,
            auto_learned=True,
            disabled=False,
            suggester_id="litellm:ollama/gemma3",
            pipeline_version=1,
            sample_whois_sha256="a" * 64,
            sample_domain=DomainName("example.xyz"),
            created_at=datetime(2026, 5, 9, tzinfo=UTC),
            last_revalidated_at=None,
            revalidation_count=0,
        )
        assert r.id == 1
        assert r.auto_learned is True
        assert r.tld == "xyz"

    def test_naive_created_at_rejected(self) -> None:
        with pytest.raises(ValueError, match="tz-aware"):
            LearnedRule(
                id=1,
                tld="xyz",
                expires_regex=RegexPattern(r"(.+)"),
                date_format=DateFormat.ISO_8601,
                timezone="UTC",
                strptime_format=None,
                auto_learned=True,
                disabled=False,
                suggester_id="x",
                pipeline_version=1,
                sample_whois_sha256="b" * 64,
                sample_domain=DomainName("example.xyz"),
                created_at=datetime(2026, 5, 9),  # naive
                last_revalidated_at=None,
                revalidation_count=0,
            )

    def test_bad_sha256_rejected(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            LearnedRule(
                id=1,
                tld="xyz",
                expires_regex=RegexPattern(r"(.+)"),
                date_format=DateFormat.ISO_8601,
                timezone="UTC",
                strptime_format=None,
                auto_learned=True,
                disabled=False,
                suggester_id="x",
                pipeline_version=1,
                sample_whois_sha256="too-short",
                sample_domain=DomainName("example.xyz"),
                created_at=datetime(2026, 5, 9, tzinfo=UTC),
                last_revalidated_at=None,
                revalidation_count=0,
            )

    def test_revalidation_count_non_negative(self) -> None:
        with pytest.raises(ValueError, match="revalidation_count"):
            LearnedRule(
                id=1,
                tld="xyz",
                expires_regex=RegexPattern(r"(.+)"),
                date_format=DateFormat.ISO_8601,
                timezone="UTC",
                strptime_format=None,
                auto_learned=True,
                disabled=False,
                suggester_id="x",
                pipeline_version=1,
                sample_whois_sha256="c" * 64,
                sample_domain=DomainName("example.xyz"),
                created_at=datetime(2026, 5, 9, tzinfo=UTC),
                last_revalidated_at=None,
                revalidation_count=-1,
            )
