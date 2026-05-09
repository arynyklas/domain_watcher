# ADR 0002 — Bounded Contexts, Entities & Value Objects

> Status: **DRAFT**, awaiting approval.
> Supersedes: nothing.
> Related: [0001 Overview](./0001-overview.md).

This document is the canonical reference for the pure layer (`core/`).
Anything outside `core/` consumes it through the ports defined here.

## Conventions

- All entities are `@dataclass(frozen=True, slots=True)`. Mutation goes
  through factory methods that return a new instance.
- All value objects (VOs) are `@dataclass(frozen=True, slots=True)` and
  validate in `__post_init__`. Two VOs with the same field values are equal.
- Ports are `typing.Protocol` (structural). Adapters do not need to inherit.
- `Literal` is from `typing`; event criticality uses `Literal["critical", "standard"]`.
- Events are frozen dataclasses with an `occurred_at: datetime` (UTC).
- Time is always UTC. The system uses an injected `TimeProvider` port; no
  `datetime.utcnow()` calls in `core/`.

## 1. `core/shared/` — cross-context primitives

### Value objects

```python
@dataclass(frozen=True, slots=True)
class DomainName:
    """RFC 1035 normalized FQDN — lowercase, no trailing dot, IDN → punycode."""
    value: str

    def __post_init__(self) -> None:
        # length, label, punycode validation
        ...

    @property
    def tld(self) -> str: ...   # "ru", "co.uk" via PSL
    @property
    def registrable(self) -> "DomainName": ...  # eTLD+1 via PSL
```

```python
@dataclass(frozen=True, slots=True)
class Duration:
    seconds: int
    @classmethod
    def days(cls, n: int) -> "Duration": ...
    @classmethod
    def parse(cls, s: str) -> "Duration": ...   # "30d", "12h", "PT15M"
```

### Ports

```python
class TimeProvider(Protocol):
    def now(self) -> datetime: ...   # always tz-aware, UTC
```

### Errors

A small, layered hierarchy. `core/` never raises stdlib `Exception`
directly; adapters wrap their failures in these.

```
DomainWatcherError
├── ConfigError
├── CheckingError
│   ├── TransientCheckError
│   └── PermanentCheckError
├── ParseError
│   ├── NoMatchingRuleError       # no static or learned rule matched
│   ├── SuggestionError           # RuleSuggester transport / model failure (transient)
│   └── RuleValidationError       # candidate rule rejected by validation pipeline
└── NotificationError
    └── DeliveryFailedError
```

## 2. `core/monitoring/` — what we watch and when

### Entities

```python
@dataclass(frozen=True, slots=True)
class MonitoredDomain:
    name: DomainName
    schedule: CheckSchedule
    checker_id: str                    # e.g. "rdap"
    notify_thresholds: tuple[Duration, ...]   # e.g. (30d, 7d, 1d)
    channels: tuple[ChannelId, ...]    # which notifiers receive its alerts
    last_check: LastCheck | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    # ^ opaque bookkeeping (e.g. managed_by="bot"). NOT for tenant routing —
    # that lives in ChannelResolver (see core/notification/ports.py).

    def with_check_result(self, result: CheckResult) -> "MonitoredDomain": ...
    def is_due(self, now: datetime) -> bool: ...
```

### Value objects

```python
@dataclass(frozen=True, slots=True)
class CheckSchedule:
    cron: str                          # "0 */6 * * *"

@dataclass(frozen=True, slots=True)
class LastCheck:
    at: datetime
    outcome: CheckOutcome              # see core/checking
    expires_at: datetime | None        # None on failure

@dataclass(frozen=True, slots=True)
class ChannelId:
    value: str                         # "tg-ops", "email-team"
```

### Events

```python
@dataclass(frozen=True, slots=True)
class DomainAdded(DomainEvent):
    domain: DomainName

@dataclass(frozen=True, slots=True)
class DomainRemoved(DomainEvent):
    domain: DomainName

@dataclass(frozen=True, slots=True)
class DomainCheckRequested(DomainEvent):
    domain: DomainName
    checker_id: str
```

### Ports

```python
class MonitoredDomainRepository(Protocol):
    async def get(self, name: DomainName) -> MonitoredDomain | None: ...
    async def add(self, domain: MonitoredDomain) -> None: ...
    async def update(self, domain: MonitoredDomain) -> None: ...
    async def remove(self, name: DomainName) -> None: ...
    async def list_all(self) -> Sequence[MonitoredDomain]: ...
    async def due_for_check(self, now: datetime) -> Sequence[MonitoredDomain]: ...
```

The repo is intentionally minimal. Tenant-aware dispatch is handled by
`ChannelResolver`; persistence remains scoped to monitored-domain state.

## 3. `core/checking/` — asking a source for an expiration date

### Value objects

```python
class CheckOutcome(StrEnum):
    OK = "ok"
    TRANSIENT_ERROR = "transient_error"   # retry me
    PERMANENT_ERROR = "permanent_error"   # don't retry; surface

@dataclass(frozen=True, slots=True)
class CheckResult:
    domain: DomainName
    outcome: CheckOutcome
    expires_at: datetime | None        # required when outcome == OK
    source: str                        # "rdap", "whois", "script:foo.sh"
    raw: str | None = None             # optional; whois text for parsing audit
    error: str | None = None           # human-readable; required on errors

    def __post_init__(self) -> None:
        # invariant: OK ⇔ expires_at is not None
        ...
```

### Policies

```python
@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: Duration = Duration.seconds(1)
    factor: float = 5.0

    def delay_for(self, attempt: int) -> Duration: ...
```

### Events

```python
@dataclass(frozen=True, slots=True)
class DomainCheckCompleted(DomainEvent):
    result: CheckResult

@dataclass(frozen=True, slots=True)
class DomainCheckFailed(DomainEvent):
    domain: DomainName
    source: str
    reason: str
    transient: bool
```

### Ports

```python
class ExpirationChecker(Protocol):
    """Asks a single source for `expires_at` of a domain."""
    id: ClassVar[str]                  # "rdap", "whois", "script:..."

    async def check(self, domain: DomainName) -> CheckResult: ...
```

`CheckResult` is what the checker promises. It must never return a fake
"OK" with a guessed expiration date — that breaks the contract callers
rely on. If the checker doesn't know, the result is `TRANSIENT_ERROR` or
`PERMANENT_ERROR`.

## 4. `core/parsing/` — WHOIS text → ExpirationDate

### Value objects

```python
class DateFormat(StrEnum):
    ISO_8601 = "iso8601"
    RFC_3339 = "rfc3339"
    DD_MMM_YYYY = "dd-mmm-yyyy"        # "31-Dec-2026"
    YYYY_MM_DD = "yyyy-mm-dd"
    EPOCH_SECONDS = "epoch"
    CUSTOM = "custom"                   # see strptime_format below

@dataclass(frozen=True, slots=True)
class ParseRule:
    tld: str                            # "ru", "co.uk"
    expires_regex: RegexPattern         # exactly one capture group
    date_format: DateFormat
    timezone: str = "UTC"
    strptime_format: str | None = None  # required if date_format == CUSTOM

    def __post_init__(self) -> None:
        # validate: exactly one capture group; CUSTOM ⇒ strptime_format set
        ...

@dataclass(frozen=True, slots=True)
class RegexPattern:
    raw: str
    @cached_property
    def compiled(self) -> re.Pattern[str]: ...
```

### Ports

```python
class WhoisParser(Protocol):
    """Deterministic WHOIS-text → expiration extractor.

    Pure: given the same raw text and rule set, returns the same datetime.
    """
    async def parse(
        self, raw: str, domain: DomainName, rules: Sequence[ParseRule]
    ) -> datetime: ...   # raises NoMatchingRuleError or ParseError


class RuleSuggester(Protocol):
    """Runtime LLM fallback. Produces a candidate ParseRule.

    Never persists, never validates. The application-layer ParsingService
    owns the validation pipeline (see ADR 0006). Plugins implementing this
    port cannot weaken safety rails.
    """
    id: ClassVar[str]

    async def suggest(
        self, raw_whois: str, domain: DomainName
    ) -> ParseRule: ...   # raises SuggestionError on transport/model failure


class LearnedRulesRepository(Protocol):
    """Operational state: rules learned from RuleSuggester at runtime."""
    async def for_tld(self, tld: str) -> Sequence[ParseRule]: ...
    async def add(self, rule: ParseRule, *, sample_sha256: str,
                  sample_domain: DomainName, suggester_id: str,
                  pipeline_version: int) -> None: ...
    async def disable(self, rule_id: int, reason: str) -> None: ...
    async def list_all(self, *, include_disabled: bool = False
                       ) -> Sequence[LearnedRule]: ...
    async def mark_revalidated(self, rule_id: int, at: datetime) -> None: ...
```

### Events

```python
@dataclass(frozen=True, slots=True)
class WhoisRuleLearned(DomainEvent):
    rule_id: int
    tld: str
    sample_domain: DomainName
    suggester_id: str

@dataclass(frozen=True, slots=True)
class WhoisRuleRevalidated(DomainEvent):
    rule_id: int
    tld: str

@dataclass(frozen=True, slots=True)
class WhoisRuleInvalidated(DomainEvent):
    rule_id: int
    tld: str
    reason: str

@dataclass(frozen=True, slots=True)
class ParseFailed(DomainEvent):
    domain: DomainName
    reason: str
    fallback_attempted: bool
```

The split between `WhoisParser` and `RuleSuggester` is the most important
boundary in this context. The parser is pure regex application. The
suggester is opaque LLM I/O. The application-layer `ParsingService`
bridges them and is the **only** place runtime validation happens — see
[ADR 0006](./0006-runtime-llm-fallback.md) for the full safety contract.

## 5. `core/notification/` — when and how to alert

### Entities

```python
@dataclass(frozen=True, slots=True)
class Channel:
    id: ChannelId
    notifier_id: str                   # "telegram", "email", "discord", "webhook"
    routing: Mapping[str, str] = field(default_factory=dict)
    # ^ per-recipient address data (e.g. {"chat_id": "123"}).
    #   NEVER secrets — those live in NotifierConfig.settings (see ADR 0003).
```

`Channel.routing` is opaque to core. Adapters validate its shape on first send
(lazy) since routing varies per call. Construction-time configuration
(transport secrets, connection params) lives in `NotifierConfig.settings` —
see [ADR 0003 §3](./0003-config-schema.md#3-top-level-schema). Mixing the two
is a contract violation: a leaked routing field with a token is a leaked secret.

```python
@dataclass(frozen=True, slots=True)
class Alert:
    domain: DomainName
    expires_at: datetime
    threshold: Duration                # which threshold was crossed
    severity: AlertSeverity
    cycle_id: str                      # sha256(expires_at.isoformat())[:16]
    metadata: Mapping[str, str] = field(default_factory=dict)
    # ^ copied from MonitoredDomain.metadata; bookkeeping only, NOT routing.

class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
```

### Policies

```python
@dataclass(frozen=True, slots=True)
class NotificationPolicy:
    """Decides whether a check result should produce an alert."""
    thresholds: tuple[Duration, ...]   # e.g. (30d, 14d, 7d, 1d)

    def alerts_for(
        self,
        previous: LastCheck | None,
        current: CheckResult,
        now: datetime,
    ) -> Sequence[Alert]:
        """
        Emits an alert exactly once per (domain, threshold, cycle_id) crossing.

        The policy is pure: it compares prev.expires_at to current.expires_at to
        determine *whether a crossing happened in this transition*. It does NOT
        consult the IdempotencyStore — that check happens at dispatch time, keyed
        by (domain, threshold, cycle_id, channel). A renewal produces a new
        cycle_id, so the same threshold re-fires for the new cycle.
        """
```

### Events

```python
@dataclass(frozen=True, slots=True)
class NotificationDispatched(DomainEvent):
    alert: Alert
    channel: ChannelId

@dataclass(frozen=True, slots=True)
class NotificationFailed(DomainEvent):
    alert: Alert
    channel: ChannelId
    reason: str
    attempts: int
```

### Ports

```python
class Notifier(Protocol):
    id: ClassVar[str]                  # "telegram", "email", ...

    async def send(self, alert: Alert, channel: Channel) -> None:
        """Raises DeliveryFailedError on transport failure (retryable)."""

class ChannelResolver(Protocol):
    """Resolves a MonitoredDomain to the channels that should receive its alerts.

    The default StaticChannelResolver returns one Channel per id in
    domain.channels, looked up via NotifierRegistry. The bot ships a
    tenant-aware impl that returns one Channel per active subscriber,
    each with its own `routing` (e.g. per-user telegram chat_id).

    DispatchNotificationsUseCase iterates: alerts × resolver.channels_for(domain)
    → idempotency check → notifier.send(alert, channel). Idempotency is per
    (domain, threshold, cycle_id, channel_id) so a new subscriber added
    mid-cycle still receives the alert.
    """
    async def channels_for(self, domain: MonitoredDomain) -> Sequence[Channel]: ...

class IdempotencyStore(Protocol):
    """Stops us paging the operator every 6h for a week straight.

    Keyed by (domain, threshold, cycle_id, channel). cycle_id is derived
    from the current expiration date — a renewal produces a fresh cycle
    so alerts re-fire for the next cycle automatically.
    """
    async def already_fired(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
    ) -> bool: ...

    async def record(
        self,
        domain: DomainName,
        threshold: Duration,
        cycle_id: str,
        channel: ChannelId,
        at: datetime,
    ) -> None: ...
```

`cycle_id` is a stable hash of `current.expires_at.isoformat()` truncated to 16
hex chars. NotificationPolicy computes it; dispatch passes it through to the
store. A renewal (different expires_at) yields a new cycle_id, so a
30d-threshold alert fires again for the new cycle.

## 6. Cross-context contracts

### `DomainEvent` base

```python
@dataclass(frozen=True, slots=True)
class DomainEvent:
    occurred_at: datetime              # injected via TimeProvider
    correlation_id: str                # ULID; ties together check → alert
    # Subclasses set this to "critical" when loss is unacceptable.
    # See application/event_bus.py for the two-tier delivery model.
    criticality: ClassVar[Literal["critical", "standard"]] = "standard"
```

### Event criticality

|Event class|Criticality|
|---|---|
|`NotificationFailed`|critical|
|`WhoisRuleInvalidated`|critical|
|`ParseFailed`|critical|
|`DomainCheckFailed`|critical|
|all others|standard|

Critical events MUST NOT be silently dropped by the bus. See
application/event_bus.py for the two-tier queue model.

The application layer publishes events through an `EventPublisher` port
(in-process bus by default). Subscribers in the application layer call
into core ports — **events never travel through `core/` itself**.

### Aggregate boundaries

`MonitoredDomain` is the **only aggregate root**. Its invariants:

- `notify_thresholds` is non-empty and strictly descending.
- `channels` is non-empty.
- `last_check.at >= previous_last_check.at` (monotonic).

Modifications go through `with_check_result()`. The repository persists
the new instance atomically.

`Alert` and `Channel` are entities but not aggregate roots; they belong
to `MonitoredDomain`'s decision context but are persisted independently.

## 7. What lives **outside** `core/`

These are intentionally NOT in `core/`:

| Concern               | Why not in core                                       |
| --------------------- | ----------------------------------------------------- |
| HTTP client (httpx)   | I/O. Lives in `infrastructure/checkers/rdap.py`.      |
| Pydantic models       | Validation lib. Used at config + DTO boundaries only. |
| SQLAlchemy entities   | ORM mapping. Lives in `infrastructure/persistence/`.  |
| Cron parsing          | Lives in `infrastructure/scheduling/apscheduler.py`.  |
| `print` / `logging`   | Use injected `Logger` port. Adapters do real I/O.     |

If a contributor finds themselves importing `httpx`, `sqlalchemy`,
`apscheduler`, or `pydantic` inside `core/`, they have made a mistake.
A linter rule (`import-linter`) will enforce this in CI.

## 8. Test boundaries

- `tests/unit/core/` — pure tests. No fakes for I/O because there is no
  I/O. Must run in <1 s without network or filesystem.
- `tests/unit/application/` — use cases against in-memory adapters
  (memory repo, fake notifier, fake checker). Test orchestration logic.
- `tests/integration/` — real adapters against test doubles of external
  systems (mailpit for SMTP, local RDAP fixture, Postgres in container).
- `tests/e2e/` — full CLI runs against a fake clock; assert events
  emitted and notifications "delivered" to a recording notifier.

The boundary tells you what to mock. **Inside core, mock nothing.**

NotificationPolicy tests are pure: prev/current/now in, alerts list out. Tests
for the idempotency *store* live separately and exercise the (domain, threshold,
cycle_id, channel) key.
