"""Shared fakes for application-layer unit tests.

Each fake is a hand-rolled stub of the corresponding ``core/`` Protocol —
no third-party mocking library, no magic. Tests instantiate, configure
behavior fields, and inspect the recorded calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.shared.errors import DeliveryFailedError, NoMatchingRuleError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.monitoring.value_objects import ChannelId
    from domain_watcher.core.notification.entities import Alert, Channel
    from domain_watcher.core.parsing.value_objects import ParseRule
    from domain_watcher.core.shared.events import DomainEvent
    from domain_watcher.core.shared.value_objects import DomainName, Duration


class FakePublisher:
    """Records every published event."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)


class FakeMonitoredRepo:
    def __init__(self) -> None:
        self.store: dict[str, MonitoredDomain] = {}

    async def get(self, name: DomainName) -> MonitoredDomain | None:
        return self.store.get(name.value)

    async def add(self, domain: MonitoredDomain) -> None:
        self.store[domain.name.value] = domain

    async def update(self, domain: MonitoredDomain) -> None:
        self.store[domain.name.value] = domain

    async def remove(self, name: DomainName) -> None:
        self.store.pop(name.value, None)

    async def list_all(self) -> Sequence[MonitoredDomain]:
        return tuple(self.store.values())

    async def due_for_check(self, now: datetime) -> Sequence[MonitoredDomain]:
        return tuple(d for d in self.store.values() if d.is_due(now))


@dataclass
class FakeChecker:
    id: ClassVar[str] = "fake"
    results: list[CheckResult] = field(default_factory=list)
    exceptions: list[BaseException | None] = field(default_factory=list)
    calls: list[DomainName] = field(default_factory=list)

    async def check(self, domain: DomainName) -> CheckResult:
        self.calls.append(domain)
        if self.exceptions:
            exc = self.exceptions.pop(0)
            if exc is not None:
                raise exc
        if not self.results:
            raise AssertionError("FakeChecker exhausted")
        return self.results.pop(0)


def make_ok_result(domain: DomainName, expires_at: datetime) -> CheckResult:
    return CheckResult(
        domain=domain,
        outcome=CheckOutcome.OK,
        expires_at=expires_at,
        source="fake",
        raw=None,
        error=None,
    )


def make_transient_result(domain: DomainName) -> CheckResult:
    return CheckResult(
        domain=domain,
        outcome=CheckOutcome.TRANSIENT_ERROR,
        expires_at=None,
        source="fake",
        raw=None,
        error="transient",
    )


def make_permanent_result(domain: DomainName) -> CheckResult:
    return CheckResult(
        domain=domain,
        outcome=CheckOutcome.PERMANENT_ERROR,
        expires_at=None,
        source="fake",
        raw=None,
        error="permanent",
    )


@dataclass
class FakeNotifier:
    id: ClassVar[str] = "fake"
    behavior: list[BaseException | None] = field(default_factory=list)
    calls: list[tuple[Alert, Channel]] = field(default_factory=list)

    async def send(self, alert: Alert, channel: Channel) -> None:
        self.calls.append((alert, channel))
        if self.behavior:
            exc = self.behavior.pop(0)
            if exc is not None:
                raise exc


class FakeIdempotency:
    def __init__(self) -> None:
        self.fired: set[tuple[str, int, str, str]] = set()

    @staticmethod
    def _key(
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
    ) -> tuple[str, int, str, str]:
        return (domain.value, threshold.seconds, cycle_id, channel.value)

    async def already_fired(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
    ) -> bool:
        return self._key(domain, threshold, cycle_id, channel) in self.fired

    async def record(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
        at: datetime,
    ) -> None:
        self.fired.add(self._key(domain, threshold, cycle_id, channel))


class FakeChannelResolver:
    def __init__(self, channels_by_domain: dict[str, tuple[Channel, ...]]) -> None:
        self._map = channels_by_domain

    async def channels_for(self, domain: MonitoredDomain) -> Sequence[Channel]:
        return self._map.get(domain.name.value, ())


# Parsing test scaffolding ----------------------------------------------------


class FakeWhoisParser:
    """Routes raw text to a list of (rule_predicate → datetime) handlers."""

    def __init__(self) -> None:
        self.expected: list[tuple[str, datetime]] = []  # (raw, datetime)

    def map_raw(self, raw: str, expires_at: datetime) -> None:
        self.expected.append((raw, expires_at))

    async def parse(
        self,
        raw: str,
        domain: DomainName,
        rules: Sequence[ParseRule],
    ) -> datetime:
        if not rules:
            raise NoMatchingRuleError(f"no rules for {domain.tld}")
        for r, dt in self.expected:
            if r == raw:
                return dt
        raise NoMatchingRuleError(f"no rule matched for {domain.tld}")


class FakeLearnedRules:
    def __init__(self) -> None:
        self.by_tld: dict[str, list[ParseRule]] = {}
        self.added: list[tuple[ParseRule, str, DomainName, str, int]] = []
        self.disabled: list[tuple[int, str]] = []
        self.revalidated: list[tuple[int, datetime]] = []
        self.all_rules: list = []

    async def for_tld(self, tld: str) -> Sequence[ParseRule]:
        return tuple(self.by_tld.get(tld, ()))

    async def add(
        self,
        rule: ParseRule,
        *,
        sample_sha256: str,
        sample_domain: DomainName,
        suggester_id: str,
        pipeline_version: int,
    ) -> int:
        rule_id = len(self.added) + 1
        self.added.append((rule, sample_sha256, sample_domain, suggester_id, pipeline_version))
        self.by_tld.setdefault(rule.tld, []).append(rule)
        return rule_id

    async def disable(self, rule_id: int, reason: str) -> None:
        self.disabled.append((rule_id, reason))

    async def list_all(self, *, include_disabled: bool = False):
        return tuple(self.all_rules)

    async def mark_revalidated(self, rule_id: int, at: datetime) -> None:
        self.revalidated.append((rule_id, at))


@dataclass
class FakeSuggester:
    id: ClassVar[str] = "fake-llm"
    next_rule: ParseRule | None = None
    raises: BaseException | None = None
    calls: list[tuple[str, DomainName]] = field(default_factory=list)

    async def suggest(self, raw_whois: str, domain: DomainName) -> ParseRule:
        self.calls.append((raw_whois, domain))
        if self.raises is not None:
            raise self.raises
        if self.next_rule is None:
            raise AssertionError("FakeSuggester.next_rule unset")
        return self.next_rule


@dataclass
class FakeValidationPipeline:
    pipeline_version: ClassVar[int] = 1
    behavior: BaseException | None = None
    calls: list[tuple[ParseRule, str, DomainName]] = field(default_factory=list)

    async def validate(
        self,
        rule: ParseRule,
        *,
        raw_whois: str,
        domain: DomainName,
    ) -> None:
        self.calls.append((rule, raw_whois, domain))
        if self.behavior is not None:
            raise self.behavior


@dataclass
class FakeLimiter:
    """``acquire`` returns ``True`` until the budget is exhausted."""

    budget: int = 1_000_000
    seen: list[str] = field(default_factory=list)

    async def acquire(self, key: str) -> bool:
        self.seen.append(key)
        if self.budget <= 0:
            return False
        self.budget -= 1
        return True


__all__ = [
    "DeliveryFailedError",
    "FakeChannelResolver",
    "FakeChecker",
    "FakeIdempotency",
    "FakeLearnedRules",
    "FakeLimiter",
    "FakeMonitoredRepo",
    "FakeNotifier",
    "FakePublisher",
    "FakeSuggester",
    "FakeValidationPipeline",
    "FakeWhoisParser",
    "make_ok_result",
    "make_permanent_result",
    "make_transient_result",
]
