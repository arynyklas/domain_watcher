# Domain Watcher — Architecture Overview

> Status: **DRAFT**, awaiting approval.
> Last updated: 2026-05-09

## 1. Purpose

Domain Watcher is a small but production-grade system that periodically checks
when a domain name expires and notifies the operator before it does. It is
designed to be used in two ways:

1. As a **standalone self-hosted service** (Docker), driven by a hot-reloadable
   YAML config.
2. As an **importable Python library** that other applications embed
   (specifically: a multi-tenant Telegram bot in a sibling repository).

This document describes the core repository (`domain_watcher`). The Telegram
bot lives in a separate repository (`domain_watcher_bot`) and depends on this
one as a library — see [§9](#9-companion-repository-domain_watcher_bot).

## 2. Naming convention (important)

The word *domain* is overloaded:

- **Domain (DNS)** — the thing we monitor, e.g. `example.com`. In code we call
  this `MonitoredDomain` (entity) or `DomainName` (value object).
- **Domain (DDD)** — the pure business-logic layer. In code we call this
  package `core/`, **not** `domain/`, to avoid the clash. Application,
  infrastructure, and interfaces sit around it as usual.

Whenever this document says *domain* unqualified, it means the DNS concept.

## 3. Goals and non-goals

### Goals

- Multiple checking strategies: **RDAP**, **WHOIS**, **custom user script**.
- Multiple notification channels: **Telegram**, **Email (SMTP)**,
  **Discord webhook**, **generic HTTP webhook**.
- **Hot-reloadable** configuration (add/remove domains and channels with
  zero downtime).
- WHOIS parsing that is deterministic at runtime, with an *optional* one-shot
  LLM helper invoked via **LiteLLM** (any provider; default `ollama/gemma3`)
  for an unfamiliar TLD — never on the hot path.
- Extensibility: new checkers / notifiers / parsers can be registered by
  third-party packages without forking the core.
- Clean public Python API so a downstream consumer (the Telegram bot) does
  not reach into internals.

### Non-goals

- A general DNS monitoring suite (we monitor expiration only — not A/AAAA
  health, certificates, etc.). Adjacent checks are easy to add later via the
  plugin interface, but they are out of scope for v1.
- A web UI.
- Multi-tenant user management. That belongs to the bot repo.

## 4. Architectural style

Hexagonal (Ports & Adapters) with light DDD bounded contexts. Three layers:

```
                     ┌─────────────────────────────────────────┐
                     │              interfaces/                │
                     │  (CLI, library API, optional HTTP)      │
                     └──────────────────┬──────────────────────┘
                                        │ calls
                     ┌──────────────────▼──────────────────────┐
                     │             application/                │
                     │   use cases, orchestration, scheduler   │
                     └──────────────────┬──────────────────────┘
                                        │ depends on (ports)
                     ┌──────────────────▼──────────────────────┐
                     │                core/                    │
                     │  pure logic, entities, VOs, policies,   │
                     │  domain events, port (Protocol) defs    │
                     └──────────────────▲──────────────────────┘
                                        │ implements (adapters)
                     ┌──────────────────┴──────────────────────┐
                     │           infrastructure/               │
                     │ rdap, whois, smtp, telegram, postgres,  │
                     │ sqlite, watchdog, apscheduler, litellm  │
                     └─────────────────────────────────────────┘
```

Rules:

- `core/` imports nothing outside the standard library and `core/` itself.
- `application/` imports `core/` only.
- `infrastructure/` imports `core/` and may pull third-party libs.
- `interfaces/` wires everything via `composition.py`.

This is the standard onion. We adopt it because the system is I/O-shaped:
the same use case must run against memory, SQLite, or Postgres; the same
expiring-domain event must fan out to four notifiers; the same WHOIS data
must be parsable by a dozen TLD-specific rules. Ports protect us from each
of those axes leaking into the others.

## 5. Bounded contexts

Inside `core/` we group by **bounded context**, not by technical layer.
Files that change together live together.

| Context        | Responsibility                                                 |
| -------------- | -------------------------------------------------------------- |
| `monitoring`   | What domains we watch, when they were last checked, schedule.  |
| `checking`     | The act of asking *some source* for an expiration date.        |
| `parsing`      | Turning raw WHOIS text into a structured `ExpirationDate`.     |
| `notification` | Deciding *when* to alert and dispatching alerts.               |
| `shared`       | Cross-context value objects (`DomainName`, `Duration`).        |

Each has its own `entities.py`, `value_objects.py`, `events.py`, `ports.py`,
`policies.py` as needed — only the files that earn their keep.

## 6. Repository layout

```
domain_watcher/
├── docs/                         # English-only docs (per requirement)
│   ├── architecture/             # this folder; ADRs and overview
│   ├── guides/                   # operator + integrator guides
│   └── reference/                # auto-generated API ref (mkdocs)
├── src/domain_watcher/
│   ├── core/
│   │   ├── monitoring/
│   │   │   ├── entities.py       # MonitoredDomain aggregate
│   │   │   ├── value_objects.py  # CheckSchedule, LastCheck
│   │   │   ├── events.py         # DomainCheckRequested, ...
│   │   │   └── ports.py          # MonitoredDomainRepository
│   │   ├── checking/
│   │   │   ├── value_objects.py  # CheckResult, CheckOutcome (Ok|Transient|Permanent)
│   │   │   ├── policies.py       # retry/backoff
│   │   │   ├── events.py         # DomainCheckCompleted, DomainCheckFailed
│   │   │   └── ports.py          # ExpirationChecker (Protocol)
│   │   ├── parsing/
│   │   │   ├── value_objects.py  # ParseRule, RegexPattern, DateFormat, LearnedRule
│   │   │   ├── events.py         # WhoisRuleLearned, WhoisRuleInvalidated, ParseFailed
│   │   │   └── ports.py          # WhoisParser, RuleSuggester, LearnedRulesRepository
│   │   ├── notification/
│   │   │   ├── entities.py       # Alert, Channel
│   │   │   ├── policies.py       # NotificationPolicy (e.g. 30d/7d/1d)
│   │   │   ├── events.py         # NotificationDispatched, NotificationFailed
│   │   │   └── ports.py          # Notifier (Protocol)
│   │   └── shared/
│   │       ├── value_objects.py  # DomainName, Duration, TimeProvider
│   │       └── errors.py
│   ├── application/
│   │   ├── use_cases/
│   │   │   ├── check_domain.py
│   │   │   ├── dispatch_notifications.py
│   │   │   └── reload_config.py
│   │   ├── services/
│   │   │   ├── parsing_service.py    # WHOIS parser + LLM fallback orchestration (ADR 0006)
│   │   │   └── revalidation_service.py  # periodic learned-rules health check
│   │   ├── scheduling.py         # SchedulerService (port)
│   │   ├── event_bus.py          # in-process pub/sub; iterator + callback APIs
│   │   └── unit_of_work.py
│   ├── infrastructure/
│   │   ├── checkers/
│   │   │   ├── rdap.py           # IANA bootstrap + RFC 7483
│   │   │   ├── whois.py          # raw 43/tcp + python-whois fallback
│   │   │   └── script.py         # subprocess, stdout JSON contract
│   │   ├── parsers/
│   │   │   ├── regex.py          # rule-driven, deterministic
│   │   │   ├── llm_suggester.py  # LiteLLM (default: ollama/gemma3); validated by service
│   │   │   └── validation_pipeline.py  # 6-gate ParseRule validator (ADR 0006 §4)
│   │   ├── notifiers/
│   │   │   ├── telegram.py
│   │   │   ├── email_smtp.py
│   │   │   ├── discord.py
│   │   │   └── webhook.py
│   │   ├── persistence/
│   │   │   ├── memory.py
│   │   │   ├── sqlite.py
│   │   │   ├── postgres.py       # SQLAlchemy 2 async + asyncpg
│   │   │   └── learned_rules/    # repository impl per backend
│   │   ├── scheduling/
│   │   │   └── apscheduler.py
│   │   └── config/
│   │       ├── schema.py         # Pydantic models
│   │       ├── loader.py         # YAML + env interpolation
│   │       └── watcher.py        # watchdog → ConfigHolder.update()
│   ├── interfaces/
│   │   ├── cli/
│   │   │   └── app.py            # Typer; entrypoint for Docker
│   │   ├── library/
│   │   │   └── api.py            # public DomainWatcher façade
│   │   └── http/                 # optional, behind a feature flag
│   │       └── api.py            # FastAPI; used by the bot repo
│   ├── composition.py            # DI / wiring; the only place that imports both core and infra
│   └── __init__.py               # re-exports the library API
├── tests/
│   ├── unit/                     # pure core tests, no I/O
│   ├── integration/              # real RDAP, real SMTP via mailpit, etc.
│   └── e2e/                      # full CLI run against a fake clock
├── docker/
│   ├── Dockerfile
│   └── compose.yml               # app + sqlite volume by default
├── pyproject.toml
└── README.md
```

## 7. Key design decisions

### 7.1 Plugin model — registry + optional entry points

Checkers, notifiers, and parsers register themselves into a typed registry
keyed by a string id (`"rdap"`, `"telegram"`, …). Two registration paths:

1. **Explicit** at composition time — used by the standalone app and by
   library consumers that ship their own adapters.
2. **`importlib.metadata.entry_points`** under groups
   `domain_watcher.checkers`, `domain_watcher.notifiers`,
   `domain_watcher.parsers` — used by third-party packages.

Why both: explicit is simpler and survives without setuptools metadata
(important for tests). Entry points enable a real plugin ecosystem without
forks.

### 7.2 Async-first

The system is dominated by network I/O (RDAP, WHOIS, HTTPS webhooks, SMTP,
Postgres). All ports are async. Adapters are async. The scheduler runs an
asyncio loop. There is no sync façade in v1; we add one only if a user asks.

### 7.3 WHOIS parsing — rules first, LLM as a *bootstrapper*

The hot path is deterministic. It uses a list of `ParseRule`s keyed by
TLD:

```yaml
parsing:
  whois_rules:
    - tld: ru
      expires_regex: 'paid-till:\s+(\S+)'
      date_format: iso8601
      timezone: UTC
    - tld: com
      expires_regex: 'Registry Expiry Date:\s+(\S+)'
      date_format: iso8601
```

If WHOIS for a TLD is unparseable, the system emits a `ParseFailed` event
and surfaces it loudly. Operators can then run:

```bash
domain-watcher suggest-rule --domain example.xyz
```

This invokes the `RuleSuggester` port (LiteLLM-backed; default model
`ollama/gemma3`),
shows the suggested regex + date format, and **only after the operator
approves** writes it back to the rules file. The LLM is never on the
runtime path. Reasons:

- Determinism. Same WHOIS in → same answer out, always.
- Cost / latency. WHOIS is checked thousands of times; LLM invocations
  shouldn't be.
- Auditability. The regex is a reviewable artifact in version control.

### 7.4 Hot reload

Config changes flow as:

```
config.yaml ──watchdog──► loader.load() ──► Config (frozen) ──► ConfigHolder.update()
                                                                       │
                                          subscribers (scheduler,      │
                                          notifier registry, …) ◄──────┘
```

`Config` is a frozen Pydantic model. `ConfigHolder` swaps the reference
atomically and notifies subscribers. If validation fails on reload, the
old config is kept, the error is logged at ERROR level, and the system
keeps running. **We never crash a running watcher because the operator
saved a typo.**

Domains added/removed at reload time → scheduler reconciles its job set
(new jobs scheduled, removed jobs cancelled, unchanged jobs left alone).

### 7.5 Scheduling

`apscheduler`'s `AsyncIOScheduler`. Per-domain cron expression in config,
default `"0 */6 * * *"` (every 6 hours). One job per domain → easy
reconciliation on hot reload.

We considered hand-rolled `asyncio.sleep` loops to avoid the dep; rejected
because `apscheduler` already solves clock drift, missed-fire policies, and
gives us an honest `next_run_time` for `/status`.

### 7.6 Persistence

Three repository adapters ship in the core repo:

| Adapter    | Use case                                                   |
| ---------- | ---------------------------------------------------------- |
| `memory`   | tests; very small static deployments                       |
| `sqlite`   | default for self-host; one file, no ops burden             |
| `postgres` | shared infra; needed by the bot repo                       |

The `MonitoredDomainRepository` port is intentionally narrow (CRUD +
`due_for_check(now)`). It does **not** model users or tenants — that is
the bot repo's job.

### 7.7 Domain events and notifications

Checkers emit `DomainCheckCompleted(domain, expires_at, source)` or
`DomainCheckFailed(domain, reason)`. A use case
(`DispatchNotificationsUseCase`) subscribes to these, runs them through
the configured `NotificationPolicy`, and asks the relevant `Notifier`
adapters to deliver.

Delivery has bounded retry with exponential backoff (3 attempts, 1s/5s/25s).
After exhaustion, a `NotificationFailed` event is emitted; we never
silently drop. Operators can wire that event to a "dead-letter" notifier.

### 7.8 Public library API

```python
from domain_watcher import ChannelId, DomainName, DomainWatcher
from domain_watcher.adapters import RdapChecker, TelegramNotifier

watcher = DomainWatcher.from_config_file("config.yaml")
await watcher.start()                 # standalone use

# or, programmatic use (bot repo):
watcher = DomainWatcher.builder() \
    .with_repository(my_postgres_repo) \
    .with_checker("rdap", RdapChecker()) \
    .with_notifier("tg", TelegramNotifier(token=..., chat_id=...)) \
    .build()

await watcher.check_now(DomainName("example.com"))

await watcher.ensure_watching(
    DomainName("example.com"),
    checker_id="rdap",
    channels=[ChannelId("tg-ops")],
)

# Two equivalent ways to consume events — both ship in v1:
async for event in watcher.events():               # iterator
    ...

watcher.on(DomainCheckCompleted, handle_completed) # callback
watcher.on_any(handle_any_event)
```

`ensure_watching` is idempotent: it upserts the `MonitoredDomain` and adds
(or updates) the scheduler job. `remove_watching` is its inverse; it preserves
alert idempotency history.

The bot repo uses both: an iterator drives its main alert-routing loop,
and a callback updates Prometheus metrics on every event.

### 7.9 Failure modes — explicit list

| Failure                              | Behavior                                                |
| ------------------------------------ | ------------------------------------------------------- |
| RDAP server 5xx / connection reset   | Classified `Transient`; retried by policy (3x, backoff) |
| RDAP says "no such domain"           | Classified `Permanent`; emits `DomainCheckFailed`       |
| Custom script returns non-zero       | `DomainCheckFailed`; stderr captured (truncated)        |
| Notifier transport down              | Bounded retry → `NotificationFailed` event              |
| Config invalid on reload             | Old config kept; loud error log                         |
| WHOIS unparseable, LLM fallback off  | `ParseFailed` event + log; no notification storm        |
| WHOIS unparseable, LLM fallback on   | LLM suggests rule → self-validated → stored as learned  |
| LLM-suggested rule fails validation  | `ParseFailed` event; rule **not** persisted             |
| LLM backend unreachable during fallback | `ParseFailed` event; runtime continues                  |
| Same alert would fire repeatedly     | Per-(domain, threshold) idempotency key in repo         |

The last row matters: without an idempotency record we'd page the operator
every six hours for the last week of a domain's life. That row earns
its own column in the persistence schema.

## 8. Tech stack

| Concern        | Choice                          | Why                                       |
| -------------- | ------------------------------- | ----------------------------------------- |
| Language       | Python 3.12+                    | `asyncio` ergonomics, typing improvements |
| HTTP           | `httpx`                         | async + sync, good defaults               |
| WHOIS          | `python-whois`                  | per repo decision; keeps the pure layer thin |
| Scheduler      | `apscheduler`                   | mature, async-aware                       |
| Hot reload     | `watchdog`                      | portable file events                      |
| Config         | `pydantic` v2 + `pydantic-settings` | typed config, env interpolation       |
| CLI            | `typer`                         | minimal boilerplate                       |
| Telegram (core)| `httpx` → Bot API HTTP          | core ships a *single-channel* notifier; **no** aiogram in this repo |
| Logging        | `structlog`                     | structured logs, easy to ship to ELK      |
| ORM (postgres) | SQLAlchemy 2 async + `asyncpg`  | repo 2 will need this                     |
| ORM (sqlite)   | SQLAlchemy 2 async + `aiosqlite`| same                                      |
| Tests          | `pytest` + `pytest-asyncio`     | standard                                  |
| LLM (suggester)| `litellm`                       | unified API to ~100 providers; default `ollama/gemma3` |
| Packaging      | `uv` + `pyproject.toml`         | fast, lockfile, modern                    |
| Container      | distroless or slim Debian       | small image, no shell exposure            |

## 9. Companion repository: `domain_watcher_bot` (separate repo)

> **Hard boundary.** The bot is a **separate repository**, not a directory of
> this one. **No bot code, no aiogram dependency, no `users`/`subscriptions`
> tables ship in this repo.** ADR 0005 specifies the bot's design so the bot
> repo can be built against a stable public API surface — it is not an
> in-repo deliverable.

The bot consumes `domain_watcher` from PyPI (or git pin) and uses the
public library API only.

The bot owns:

- Telegram-specific concerns (handlers, FSM, command parsing)
- A Postgres schema for `users`, `subscriptions`, `user_domains`
- Rate limits and quotas (e.g. "free tier = 5 domains")
- Mapping between TG user → notification channel

The bot **does not**:

- Reimplement RDAP/WHOIS — it uses `RdapChecker`/`WhoisChecker`.
- Reach into `core/` — it only uses `interfaces/library/api.py`.
- Define its own event types — it consumes `DomainCheckCompleted` etc.

Wiring sketch:

```python
# bot/composition.py
watcher = DomainWatcher.builder() \
    .with_repository(PgMonitoredDomainRepo(session_factory)) \
    .with_checker("rdap", RdapChecker()) \
    .with_notifier("tg-multitenant", TenantTelegramNotifier(bot, user_resolver)) \
    .build()
```

`PgMonitoredDomainRepo` presents one `MonitoredDomain` per FQDN. Subscribers
live in a bot-side `subscriptions` table; the repo collapses N subscribers
into one row (see [ADR 0005](./0005-bot-integration.md)).

The bot ships a tenant-aware `ChannelResolver` (defined in
`core/notification/ports.py`; see [ADR 0002](./0002-bounded-contexts.md)) that
returns one `Channel` per active subscriber, each carrying
`routing = {"chat_id": "<tg_id>"}`. `TenantTelegramNotifier` reads
`channel.routing["chat_id"]` for each call. Idempotency is enforced per
`(domain, threshold, cycle_id, channel_id)`, so adding a new subscriber
mid-cycle still alerts them.

The bot does not embed routing data into `MonitoredDomain.metadata`.
Per-recipient routing (e.g., Telegram `chat_id`) is carried on
`Channel.routing` — a typed-but-opaque per-call mapping defined in ADR 0002 §5.
`metadata` is reserved for bookkeeping (for example, `managed_by="bot"`) and
never used for routing decisions.

The bot repo gets the same DDD/hexagonal layout, just with `core/users/`
and `core/subscriptions/` as its bounded contexts.

## 10. Tradeoffs deliberately accepted

- **In-process event bus** rather than Redis/NATS. Adds a port boundary
  (`EventPublisher`) so we can swap later if the bot needs cross-process
  fanout. v1 stays single-process.
- **No web UI in v1.** The CLI + config file cover self-host; the bot repo
  is the UI for end users.
- **Pydantic v2 in the core layer's *boundaries*** (config, DTOs) but
  **not** in `core/` entities. Entities are plain `@dataclass(frozen=True)`
  to keep the pure layer dep-free.
- **Postgres adapter ships in the core repo**, even though only the bot
  needs it today. Centralizing all generic persistence keeps the bot repo
  tenant-focused.
- **In-memory scheduler.** APScheduler runs in-process; a crash mid-check loses
  at most one in-flight check per domain. Checks are idempotent and re-run on
  the next scheduled tick. v1 does not persist scheduler state.

## 11. Resolved decisions

The following choices are now locked in (recorded for future readers):

1. **Config source of truth (standalone).** YAML file. The DB stores only
   operational state: `last_check`, idempotency keys, retry counters,
   learned WHOIS rules. The bot repo overrides this — its DB is its
   source of truth (see [ADR 0005](./0005-bot-integration.md)).
2. **Event API surface.** Both. Public API exposes an async iterator
   `async for evt in watcher.events()` *and* a callback registration
   `watcher.on(DomainCheckCompleted, handler)`. They share one
   `EventPublisher` internally; both are first-class.
3. **HTTP interface.** Skipped in v1. The bot embeds the watcher
   in-process in the same container.
4. **License.** MIT.
5. **LLM placement.** Runtime fallback (not design-time only). When
   no regex rule matches, `RuleSuggester` is invoked to produce a
   `ParseRule`, which is **self-validated against the same WHOIS text**,
   stored in the `learned_rules` state table with `auto_learned = true`,
   and reused for future checks. See [ADR 0006](./0006-runtime-llm-fallback.md)
   for the full safety contract — this is the most error-prone surface
   in the system, and the safety rails matter.

---

## Index of architecture documents

- `0001-overview.md` — this document
- [`0002-bounded-contexts.md`](./0002-bounded-contexts.md) — entities, VOs, events, ports per context
- [`0003-config-schema.md`](./0003-config-schema.md) — full YAML schema, hot reload, validation
- [`0004-plugin-protocol.md`](./0004-plugin-protocol.md) — checker/notifier/parser/suggester contracts
- [`0005-bot-integration.md`](./0005-bot-integration.md) — bot repo wiring in detail
- [`0006-runtime-llm-fallback.md`](./0006-runtime-llm-fallback.md) — safety contract for runtime LLM rule learning

Implementation plan to follow in `docs/plans/`.
