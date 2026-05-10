"""Pydantic schema validation (ADR 0003 §4 + §6)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from domain_watcher.core.parsing.value_objects import DateFormat
from domain_watcher.core.shared.value_objects import Duration
from domain_watcher.infrastructure.config.schema import (
    CheckerConfig,
    Config,
    DomainEntry,
    LlmFallbackConfig,
    NotificationDefaults,
    NotifierConfig,
    ParsingConfig,
    SuggesterConfig,
    WhoisRule,
)


# ---------------------------------------------------------------------------
# Duration field
# ---------------------------------------------------------------------------
def test_duration_field_accepts_string() -> None:
    nd = NotificationDefaults.model_validate({"thresholds": ("30d", "7d", "1d")})
    assert nd.thresholds[0].seconds == 30 * 86400


def test_duration_field_accepts_duration_instance() -> None:
    nd = NotificationDefaults(
        thresholds=(Duration.days(30), Duration.days(7), Duration.days(1))
    )
    assert nd.thresholds[1].seconds == 7 * 86400


def test_duration_field_accepts_int_seconds() -> None:
    nd = NotificationDefaults.model_validate({"thresholds": (86400, 60, 1)})
    assert nd.thresholds[0] == Duration.from_seconds(86400)


def test_duration_field_rejects_garbage() -> None:
    with pytest.raises(ValidationError) as exc:
        NotificationDefaults.model_validate({"thresholds": ("seven days", "1d")})
    assert "Duration" in str(exc.value)


# ---------------------------------------------------------------------------
# WhoisRule
# ---------------------------------------------------------------------------
def test_whois_rule_requires_one_capture_group() -> None:
    with pytest.raises(ValidationError) as exc:
        WhoisRule(
            tld="com",
            expires_regex=r"Registry Expiry Date:\s+\S+",  # zero groups
            date_format=DateFormat.ISO_8601,
        )
    assert "exactly 1 capture group" in str(exc.value)


def test_whois_rule_rejects_invalid_regex() -> None:
    with pytest.raises(ValidationError) as exc:
        WhoisRule(
            tld="com",
            expires_regex=r"(unbalanced",
            date_format=DateFormat.ISO_8601,
        )
    assert "invalid regex" in str(exc.value)


def test_whois_rule_custom_requires_strptime() -> None:
    with pytest.raises(ValidationError) as exc:
        WhoisRule(
            tld="com",
            expires_regex=r"(\S+)",
            date_format=DateFormat.CUSTOM,
        )
    assert "strptime_format" in str(exc.value)


def test_whois_rule_iso_rejects_strptime() -> None:
    with pytest.raises(ValidationError):
        WhoisRule(
            tld="com",
            expires_regex=r"(\S+)",
            date_format=DateFormat.ISO_8601,
            strptime_format="%Y",
        )


def test_whois_rule_tld_must_be_lowercase() -> None:
    with pytest.raises(ValidationError):
        WhoisRule(
            tld="COM",  # uppercase rejected — registries normalize lowercase
            expires_regex=r"(\S+)",
            date_format=DateFormat.ISO_8601,
        )


# ---------------------------------------------------------------------------
# Webhook template eager validation
# ---------------------------------------------------------------------------
def test_webhook_template_known_placeholders_pass() -> None:
    NotifierConfig(
        id="pd",
        type="webhook",
        settings={
            "url": "https://example.com",
            "body_template": "${domain} ${threshold} ${cycle_id}",
        },
    )


def test_webhook_template_unknown_placeholder_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        NotifierConfig(
            id="pd",
            type="webhook",
            settings={
                "url": "https://example.com",
                "body_template": "${domain} ${nope}",
            },
        )
    assert "nope" in str(exc.value)


def test_webhook_requires_url_and_template() -> None:
    with pytest.raises(ValidationError):
        NotifierConfig(
            id="pd",
            type="webhook",
            settings={"body_template": "${domain}"},
        )
    with pytest.raises(ValidationError):
        NotifierConfig(
            id="pd",
            type="webhook",
            settings={"url": "https://example.com"},
        )


def test_non_webhook_settings_are_opaque() -> None:
    n = NotifierConfig(
        id="tg",
        type="telegram",
        settings={"bot_token": "abc", "chat_id": "123"},
    )
    assert n.settings["bot_token"] == "abc"


# ---------------------------------------------------------------------------
# Cron validation
# ---------------------------------------------------------------------------
def test_cron_must_have_five_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        DomainEntry(
            name="example.com",
            checker="rdap",
            schedule="0 6 * *",  # 4 fields
            channels=("tg",),
        )
    assert "5 whitespace-separated fields" in str(exc.value)


def test_cron_rejects_alpha_garbage() -> None:
    with pytest.raises(ValidationError) as exc:
        DomainEntry(
            name="example.com",
            checker="rdap",
            schedule="abc def ghi jkl mno",  # 5 tokens, all alpha
            channels=("tg",),
        )
    assert "invalid characters" in str(exc.value)


def test_cron_accepts_step_syntax() -> None:
    d = DomainEntry(
        name="example.com",
        checker="rdap",
        schedule="0 */6 * * *",
        channels=("tg",),
    )
    assert d.schedule == "0 */6 * * *"


# ---------------------------------------------------------------------------
# Thresholds invariants
# ---------------------------------------------------------------------------
def test_domain_thresholds_must_be_descending() -> None:
    with pytest.raises(ValidationError) as exc:
        DomainEntry.model_validate(
            {
                "name": "example.com",
                "checker": "rdap",
                "schedule": "0 */6 * * *",
                "channels": ("tg",),
                "thresholds": ("7d", "30d"),
            }
        )
    assert "strictly descending" in str(exc.value)


def test_notification_defaults_thresholds_descending() -> None:
    with pytest.raises(ValidationError):
        NotificationDefaults.model_validate({"thresholds": ("7d", "30d")})


def test_notification_defaults_thresholds_non_empty() -> None:
    with pytest.raises(ValidationError):
        NotificationDefaults(thresholds=())


# ---------------------------------------------------------------------------
# Config-level cross-references
# ---------------------------------------------------------------------------
def _minimal_config(**overrides: object) -> Config:
    base: dict[str, object] = {
        "version": 1,
        "checkers": (CheckerConfig(id="rdap", type="rdap"),),
        "notifiers": (
            NotifierConfig(
                id="tg", type="telegram", settings={"bot_token": "x", "chat_id": "1"}
            ),
        ),
        "domains": (
            DomainEntry(
                name="example.com",
                checker="rdap",
                schedule="0 */6 * * *",
                channels=("tg",),
            ),
        ),
    }
    base |= overrides
    return Config.model_validate(base)


def test_minimal_config_validates() -> None:
    cfg = _minimal_config()
    assert cfg.version == 1
    assert cfg.runtime.log_level == "INFO"


def test_unknown_checker_reference_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _minimal_config(
            domains=(
                DomainEntry(
                    name="example.com",
                    checker="rdao",  # typo
                    schedule="0 */6 * * *",
                    channels=("tg",),
                ),
            ),
        )
    assert "rdao" in str(exc.value)


def test_unknown_channel_reference_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _minimal_config(
            domains=(
                DomainEntry(
                    name="example.com",
                    checker="rdap",
                    schedule="0 */6 * * *",
                    channels=("missing",),
                ),
            ),
        )
    assert "missing" in str(exc.value)


def test_duplicate_checker_id_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _minimal_config(
            checkers=(
                CheckerConfig(id="rdap", type="rdap"),
                CheckerConfig(id="rdap", type="rdap"),
            ),
        )
    assert re.search(r"checkers.+duplicate.+rdap", str(exc.value))


def test_duplicate_notifier_id_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _minimal_config(
            notifiers=(
                NotifierConfig(
                    id="tg",
                    type="telegram",
                    settings={"bot_token": "x", "chat_id": "1"},
                ),
                NotifierConfig(
                    id="tg",
                    type="telegram",
                    settings={"bot_token": "y", "chat_id": "2"},
                ),
            ),
        )
    assert "tg" in str(exc.value)


def test_duplicate_domain_name_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _minimal_config(
            domains=(
                DomainEntry(
                    name="example.com",
                    checker="rdap",
                    schedule="0 */6 * * *",
                    channels=("tg",),
                ),
                DomainEntry(
                    name="example.com",
                    checker="rdap",
                    schedule="0 */12 * * *",
                    channels=("tg",),
                ),
            ),
        )
    assert "example.com" in str(exc.value)


def test_duplicate_whois_tld_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _minimal_config(
            parsing=ParsingConfig(
                whois_rules=(
                    WhoisRule(
                        tld="com",
                        expires_regex=r"(\S+)",
                        date_format=DateFormat.ISO_8601,
                    ),
                    WhoisRule(
                        tld="com",
                        expires_regex=r"(\S+)",
                        date_format=DateFormat.ISO_8601,
                    ),
                ),
            ),
        )
    assert "com" in str(exc.value)


def test_extra_keys_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        Config.model_validate(
            {
                "version": 1,
                "checkers": [],
                "notifiers": [],
                "domains": [],
                "rogue_field": True,
            }
        )
    assert "rogue_field" in str(exc.value)


def test_config_is_frozen() -> None:
    cfg = _minimal_config()
    with pytest.raises(ValidationError):
        cfg.runtime.log_level = "DEBUG"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------
def test_llm_fallback_enabled_requires_suggester() -> None:
    with pytest.raises(ValidationError) as exc:
        LlmFallbackConfig(enabled=True)
    assert "suggester" in str(exc.value)


def test_llm_fallback_enabled_with_suggester() -> None:
    LlmFallbackConfig(
        enabled=True,
        suggester=SuggesterConfig(type="litellm", settings={"model": "ollama/gemma3"}),
    )


# ---------------------------------------------------------------------------
# Loading from fixtures (parses + validates the YAML files in tests/fixtures/config)
# ---------------------------------------------------------------------------
def test_valid_yaml_fixture_parses() -> None:
    """``valid.yaml`` must validate against the schema after env interpolation."""
    import os
    from pathlib import Path

    import yaml

    fixture = Path(__file__).parents[3] / "fixtures" / "config" / "valid.yaml"
    raw = fixture.read_text()
    # crude env interpolation just for this fixture-only test
    os.environ.setdefault("TG_BOT_TOKEN", "test-bot-token")
    os.environ.setdefault("TG_OPS_CHAT", "12345")
    os.environ.setdefault("PD_TOKEN", "secret")
    for var in ("TG_BOT_TOKEN", "TG_OPS_CHAT", "PD_TOKEN"):
        raw = raw.replace(f"${{{var}}}", os.environ[var])

    data = yaml.safe_load(raw)
    cfg = Config.model_validate(data)
    assert cfg.version == 1
    assert {c.id for c in cfg.checkers} == {"rdap", "whois"}
    assert {n.id for n in cfg.notifiers} == {"tg-ops", "pagerduty"}
    assert len(cfg.domains) == 2
