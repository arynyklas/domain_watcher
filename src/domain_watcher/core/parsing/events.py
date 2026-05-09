"""Events emitted by the parsing bounded context (ADR 0002 §4, ADR 0006)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from domain_watcher.core.shared.events import DomainEvent

if TYPE_CHECKING:
    from domain_watcher.core.shared.events import Criticality
    from domain_watcher.core.shared.value_objects import DomainName


@dataclass(frozen=True, slots=True)
class WhoisRuleLearned(DomainEvent):
    """A new ``LearnedRule`` was persisted via the LLM fallback path."""

    rule_id: int = 0
    tld: str = ""
    sample_domain: DomainName | None = None
    suggester_id: str = ""

    def __post_init__(self) -> None:
        if self.rule_id <= 0:
            raise ValueError("WhoisRuleLearned.rule_id must be > 0")
        if not self.tld:
            raise ValueError("WhoisRuleLearned.tld is required")
        if self.sample_domain is None:
            raise ValueError("WhoisRuleLearned.sample_domain is required")
        if not self.suggester_id:
            raise ValueError("WhoisRuleLearned.suggester_id is required")


@dataclass(frozen=True, slots=True)
class WhoisRuleRevalidated(DomainEvent):
    """A periodic revalidation pass confirmed a learned rule."""

    rule_id: int = 0
    tld: str = ""

    def __post_init__(self) -> None:
        if self.rule_id <= 0:
            raise ValueError("WhoisRuleRevalidated.rule_id must be > 0")
        if not self.tld:
            raise ValueError("WhoisRuleRevalidated.tld is required")


@dataclass(frozen=True, slots=True)
class WhoisRuleInvalidated(DomainEvent):
    """A previously-learned rule no longer validates and was disabled."""

    rule_id: int = 0
    tld: str = ""
    reason: str = ""

    criticality: ClassVar[Criticality] = "critical"

    def __post_init__(self) -> None:
        if self.rule_id <= 0:
            raise ValueError("WhoisRuleInvalidated.rule_id must be > 0")
        if not self.tld:
            raise ValueError("WhoisRuleInvalidated.tld is required")
        if not self.reason:
            raise ValueError("WhoisRuleInvalidated.reason is required")


@dataclass(frozen=True, slots=True)
class ParseFailed(DomainEvent):
    """WHOIS parsing failed; emitted regardless of fallback being attempted."""

    domain: DomainName | None = None
    reason: str = ""
    fallback_attempted: bool = False

    criticality: ClassVar[Criticality] = "critical"

    def __post_init__(self) -> None:
        if self.domain is None:
            raise ValueError("ParseFailed.domain is required")
        if not self.reason:
            raise ValueError("ParseFailed.reason is required")


__all__ = [
    "ParseFailed",
    "WhoisRuleInvalidated",
    "WhoisRuleLearned",
    "WhoisRuleRevalidated",
]
