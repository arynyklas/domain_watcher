"""Pydantic schema for the YAML configuration (ADR 0003 §4).

The schema is the single source of truth for what the standalone app
accepts at startup and on hot reload. Every field is validated eagerly:
typos in cross-references, descending-thresholds violations, malformed
crons, regex without a capture group, and unknown webhook placeholders
all surface here — never at runtime.

``Config`` is ``frozen``; mutation produces a new instance through
``model_copy``. Subscribers diff old vs new and reconcile.
"""

from __future__ import annotations

import itertools
import re
import string as _string
from string import Template
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from domain_watcher.core.parsing.value_objects import DateFormat
from domain_watcher.core.shared.value_objects import Duration
from domain_watcher.infrastructure.config._duration_field import DurationField  # noqa: TC001

# ---------------------------------------------------------------------------
# Cron validation
# ---------------------------------------------------------------------------
# Cheap per-field syntax check — APScheduler does the deep validation when the
# job is added. We just want startup to refuse obviously broken values like
# ``"abc"`` or 4-token strings before the scheduler ever sees them.
_CRON_FIELD_RE = re.compile(r"^[0-9*/,\-?]+$")


def _validate_cron(s: str) -> str:
    parts = s.split()
    if len(parts) != 5:
        raise ValueError(f"cron must have 5 whitespace-separated fields, got {len(parts)}: {s!r}")
    for i, part in enumerate(parts):
        if not _CRON_FIELD_RE.match(part):
            raise ValueError(
                f"cron field {i} {part!r} contains invalid characters; "
                "only digits and ``* / , -`` are accepted"
            )
    return s


# ---------------------------------------------------------------------------
# Webhook template validation
# ---------------------------------------------------------------------------
_WEBHOOK_PLACEHOLDERS = frozenset({"domain", "expires_at", "threshold", "severity", "cycle_id"})


def _validate_webhook_template(template: str) -> str:
    """Validate that ``template`` only references known placeholders.

    Uses ``string.Template`` to discover placeholders so we get the exact
    same parser the runtime renderer uses.
    """
    try:
        Template(template).substitute({p: "" for p in _WEBHOOK_PLACEHOLDERS})
    except KeyError as exc:
        raise ValueError(
            f"webhook body_template references unknown placeholder ${{{exc.args[0]}}}; "
            f"allowed: {sorted(_WEBHOOK_PLACEHOLDERS)}"
        ) from exc
    except ValueError as exc:
        # Malformed ``$`` escape, e.g. ``"$"`` at end of string.
        raise ValueError(f"webhook body_template malformed: {exc}") from exc
    # Belt-and-braces: walk the template's pattern to collect identifiers and
    # double-check none escaped substitute()'s notice (e.g. ``${{stray}}``).
    placeholders = _extract_placeholders(template)
    unknown = placeholders - _WEBHOOK_PLACEHOLDERS
    if unknown:
        raise ValueError(
            f"webhook body_template references unknown placeholders {sorted(unknown)}; "
            f"allowed: {sorted(_WEBHOOK_PLACEHOLDERS)}"
        )
    return template


def _extract_placeholders(template: str) -> set[str]:
    found: set[str] = set()
    for match in Template.pattern.finditer(template):
        gd = match.groupdict()
        named = gd.get("named") or gd.get("braced")
        if named:
            found.add(named)
        elif gd.get("invalid") is not None:
            raise ValueError(
                f"webhook body_template has invalid ``$`` escape near {match.group()!r}"
            )
    return found


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
PluginId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_\-]+$", min_length=1)]
TldStr = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(\.[a-z0-9]+)*$", min_length=1)]


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------
class _Frozen(BaseModel):
    """Base for all config models — frozen + extra='forbid' by default."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


class RuntimeConfig(_Frozen):
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    timezone: NonEmptyStr = "UTC"
    state_db: NonEmptyStr = "sqlite:///state.db"


class CheckerConfig(_Frozen):
    id: PluginId
    type: NonEmptyStr
    settings: dict[str, Any] = Field(default_factory=dict)


class RetryConfig(_Frozen):
    max_attempts: Annotated[int, Field(ge=1, le=20)] = 3
    base_delay: DurationField = Field(default_factory=lambda: Duration(seconds=1))
    factor: Annotated[float, Field(gt=1.0, le=100.0)] = 5.0


class NotifierConfig(_Frozen):
    id: PluginId
    type: NonEmptyStr
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_webhook_settings(self) -> NotifierConfig:
        if self.type == "webhook":
            template = self.settings.get("body_template")
            if template is None:
                raise ValueError(f"notifier {self.id!r}: webhook requires settings.body_template")
            if not isinstance(template, str):
                raise ValueError(f"notifier {self.id!r}: settings.body_template must be a string")
            _validate_webhook_template(template)
            url = self.settings.get("url")
            if not isinstance(url, str) or not url:
                raise ValueError(f"notifier {self.id!r}: webhook requires settings.url")
        return self


class NotificationDefaults(_Frozen):
    thresholds: tuple[DurationField, ...] = Field(
        default_factory=lambda: (
            Duration.days(30),
            Duration.days(14),
            Duration.days(7),
            Duration.days(1),
        )
    )
    retry: RetryConfig = Field(default_factory=RetryConfig)

    @field_validator("thresholds")
    @classmethod
    def _strictly_descending(cls, v: tuple[Duration, ...]) -> tuple[Duration, ...]:
        if not v:
            raise ValueError("notification_defaults.thresholds cannot be empty")
        for prev, cur in itertools.pairwise(v):
            if cur.seconds >= prev.seconds:
                raise ValueError("notification_defaults.thresholds must be strictly descending")
        return v


class WhoisRule(_Frozen):
    tld: TldStr
    expires_regex: NonEmptyStr
    date_format: DateFormat
    timezone: NonEmptyStr = "UTC"
    strptime_format: str | None = None

    @field_validator("expires_regex")
    @classmethod
    def _exactly_one_group(cls, v: str) -> str:
        try:
            compiled = re.compile(v)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        if compiled.groups != 1:
            raise ValueError(
                f"whois rule expires_regex must have exactly 1 capture group, got {compiled.groups}"
            )
        return v

    @model_validator(mode="after")
    def _custom_requires_strptime(self) -> WhoisRule:
        custom = self.date_format is DateFormat.CUSTOM
        has_fmt = self.strptime_format is not None
        if custom != has_fmt:
            raise ValueError("whois rule strptime_format is required iff date_format == 'custom'")
        return self


class SuggesterConfig(_Frozen):
    type: NonEmptyStr
    settings: dict[str, Any] = Field(default_factory=dict)


class SafetyConfig(_Frozen):
    max_age_years: Annotated[int, Field(ge=1, le=200)] = 50
    min_age_seconds: Annotated[int, Field(ge=0)] = 0
    validate_on_store: bool = True
    revalidate_after: DurationField = Field(default_factory=lambda: Duration.days(30))
    max_learn_per_hour: Annotated[int, Field(ge=0, le=1000)] = 5
    max_learn_per_tld_per_24h: Annotated[int, Field(ge=0, le=1000)] = 3


class LlmFallbackConfig(_Frozen):
    enabled: bool = False
    suggester: SuggesterConfig | None = None
    safety: SafetyConfig = Field(default_factory=SafetyConfig)

    @model_validator(mode="after")
    def _suggester_required_when_enabled(self) -> LlmFallbackConfig:
        if self.enabled and self.suggester is None:
            raise ValueError(
                "parsing.llm_fallback.suggester is required when llm_fallback.enabled is true"
            )
        return self


class ParsingConfig(_Frozen):
    whois_rules: tuple[WhoisRule, ...] = Field(default_factory=tuple)
    llm_fallback: LlmFallbackConfig = Field(default_factory=LlmFallbackConfig)

    @model_validator(mode="after")
    def _unique_tlds(self) -> ParsingConfig:
        seen: set[str] = set()
        for rule in self.whois_rules:
            if rule.tld in seen:
                raise ValueError(f"parsing.whois_rules: duplicate tld {rule.tld!r}")
            seen.add(rule.tld)
        return self


class DomainEntry(_Frozen):
    name: NonEmptyStr
    checker: PluginId
    schedule: NonEmptyStr
    channels: tuple[PluginId, ...]
    thresholds: tuple[DurationField, ...] | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("schedule")
    @classmethod
    def _valid_cron(cls, v: str) -> str:
        return _validate_cron(v)

    @field_validator("channels")
    @classmethod
    def _channels_non_empty(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("domain.channels cannot be empty")
        return v

    @field_validator("thresholds")
    @classmethod
    def _thresholds_descending(cls, v: tuple[Duration, ...] | None) -> tuple[Duration, ...] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("domain.thresholds, when set, cannot be empty")
        for prev, cur in itertools.pairwise(v):
            if cur.seconds >= prev.seconds:
                raise ValueError("domain.thresholds must be strictly descending")
        return v


# ---------------------------------------------------------------------------
# Top-level Config
# ---------------------------------------------------------------------------
class Config(_Frozen):
    version: Literal[1]
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    checkers: tuple[CheckerConfig, ...]
    notifiers: tuple[NotifierConfig, ...]
    notification_defaults: NotificationDefaults = Field(default_factory=NotificationDefaults)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    domains: tuple[DomainEntry, ...]

    @model_validator(mode="after")
    def _references_resolve(self) -> Config:
        checker_ids = [c.id for c in self.checkers]
        notifier_ids = [n.id for n in self.notifiers]

        dup = _first_duplicate(checker_ids)
        if dup is not None:
            raise ValueError(f"checkers[*].id duplicate: {dup!r}")
        dup = _first_duplicate(notifier_ids)
        if dup is not None:
            raise ValueError(f"notifiers[*].id duplicate: {dup!r}")

        checker_set = set(checker_ids)
        notifier_set = set(notifier_ids)
        domain_names: set[str] = set()
        for d in self.domains:
            if d.name in domain_names:
                raise ValueError(f"domains[*].name duplicate: {d.name!r}")
            domain_names.add(d.name)
            if d.checker not in checker_set:
                raise ValueError(
                    f"domain {d.name!r}: checker {d.checker!r} is not declared in checkers[]; "
                    f"known: {sorted(checker_set)}"
                )
            for ch in d.channels:
                if ch not in notifier_set:
                    raise ValueError(
                        f"domain {d.name!r}: channel {ch!r} is not declared in notifiers[]; "
                        f"known: {sorted(notifier_set)}"
                    )
        return self


def _first_duplicate(items: list[str]) -> str | None:
    seen: set[str] = set()
    for item in items:
        if item in seen:
            return item
        seen.add(item)
    return None


# Suppress an unused-import lint when ``string`` is only used reflectively.
_ = _string

__all__ = [
    "CheckerConfig",
    "Config",
    "DomainEntry",
    "LlmFallbackConfig",
    "NotificationDefaults",
    "NotifierConfig",
    "ParsingConfig",
    "RetryConfig",
    "RuntimeConfig",
    "SafetyConfig",
    "SuggesterConfig",
    "WhoisRule",
]
