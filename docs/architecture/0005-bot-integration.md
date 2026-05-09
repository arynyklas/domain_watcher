> **READ FIRST — Hard boundary.**
> This ADR specifies the **separate** `domain_watcher_bot` repository.
> It documents the *consumer's* design so the core repo can ship a stable
> public API for it. **No code from this document — no aiogram, no `users`
> table, no Telegram FSM, no `PgMonitoredDomainRepo` — ships in the
> `domain_watcher` (core) repository.** Treat this ADR as the contract
> against which the bot repo is later built.

# ADR 0005 — Telegram Bot Repository Integration

> Status: **DRAFT**, awaiting approval.
> Integration mode: **embedded** (bot and watcher share a process and container).
> Related: [0001 Overview](./0001-overview.md),
> [0002 Bounded Contexts](./0002-bounded-contexts.md),
> [0004 Plugin Protocol](./0004-plugin-protocol.md),
> [0006 Runtime LLM Fallback](./0006-runtime-llm-fallback.md).

## 1. Repository split

| Repo                  | Responsibility                                           |
| --------------------- | -------------------------------------------------------- |
| `domain_watcher`      | Library + standalone app. **This repo.**                 |
| `domain_watcher_bot`  | Multi-tenant Telegram bot. Depends on `domain_watcher`.  |

The bot **consumes** the core via its public library API. It does not
import from `domain_watcher.core.*` or `domain_watcher.infrastructure.*`
directly — only from `domain_watcher` (the top-level re-export module)
and `domain_watcher.testing` (for tests).

A linter rule (`import-linter`) in the bot repo enforces this.

## 2. Domain model the bot adds on top

The bot has its own bounded contexts that **wrap** core concepts. Core's
`MonitoredDomain` is one-per-(domain, owner). The bot keeps that
relationship in its own schema rather than mutating core entities.

```
domain_watcher_bot/
├── src/domain_watcher_bot/
│   ├── core/
│   │   ├── users/
│   │   │   ├── entities.py             # User, TelegramId, Quota
│   │   │   ├── value_objects.py
│   │   │   ├── events.py
│   │   │   └── ports.py                # UserRepository
│   │   ├── subscriptions/
│   │   │   ├── entities.py             # Subscription (user, domain)
│   │   │   ├── policies.py             # quota enforcement
│   │   │   └── ports.py
│   │   └── shared/                     # imports DomainName from `domain_watcher`
│   ├── application/
│   │   └── use_cases/
│   │       ├── add_domain.py           # quota check → core add
│   │       ├── remove_domain.py
│   │       ├── list_my_domains.py
│   │       └── route_alert.py          # core Alert → TG message
│   ├── infrastructure/
│   │   ├── persistence/
│   │   │   ├── pg_user_repo.py
│   │   │   ├── pg_subscription_repo.py
│   │   │   └── pg_monitored_domain_repo.py    # adapts core's port
│   │   ├── telegram/
│   │   │   ├── handlers.py             # /start, /add, /remove, /list
│   │   │   ├── fsm.py                  # add-domain wizard
│   │   │   └── tenant_notifier.py      # core Notifier impl
│   │   └── migrations/                 # alembic
│   ├── interfaces/
│   │   └── bot.py                      # entry point — wires everything
│   ├── composition.py
│   └── __init__.py
├── docker/
└── docs/                               # English; same convention
```

## 3. Database schema (bot-side)

```sql
-- users.sql
CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    quota_max       INT NOT NULL DEFAULT 5,
    is_blocked      BOOLEAN NOT NULL DEFAULT false
);

-- subscriptions.sql
CREATE TABLE subscriptions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    domain_name     TEXT NOT NULL,                        -- normalized FQDN
    schedule        TEXT NOT NULL DEFAULT '0 */12 * * *',
    thresholds      TEXT[] NOT NULL DEFAULT ARRAY['30d','7d','1d'],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, domain_name)
);

-- monitored_domain_state.sql  (operational state, mirrors core's needs)
CREATE TABLE monitored_domain_state (
    domain_name     TEXT PRIMARY KEY,
    checker_id      TEXT NOT NULL,
    last_checked_at TIMESTAMPTZ,
    last_outcome    TEXT,
    expires_at      TIMESTAMPTZ
);

-- idempotency.sql  (used by core's IdempotencyStore port)
CREATE TABLE alert_idempotency (
    domain_name     TEXT NOT NULL,
    threshold_secs  BIGINT NOT NULL,
    cycle_id        TEXT NOT NULL,           -- sha256(expires_at.isoformat())[:16]
    channel_id      TEXT NOT NULL,           -- one row per recipient
    fired_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (domain_name, threshold_secs, cycle_id, channel_id)
);
```

The four-part key matters in the multi-tenant case. A new subscriber added
mid-cycle still receives the threshold alert because the (channel_id)
component differs. A renewal (new expires_at) yields a new cycle_id and
re-fires alerts for the next cycle.

Note: `subscriptions` is the **per-user** view; `monitored_domain_state`
is the **deduplicated** check state. One physical domain checked once,
fanned out to N subscribers.

## 4. Wiring sketch

```python
# domain_watcher_bot/composition.py
from domain_watcher import DomainWatcher
from domain_watcher.adapters import RdapChecker, WhoisChecker

from .infrastructure.persistence import (
    PgMonitoredDomainRepo,
    PgIdempotencyStore,
    PgUserRepo,
    PgSubscriptionRepo,
)
from .infrastructure.telegram.tenant_notifier import TenantTelegramNotifier
from .infrastructure.telegram.tenant_channel_resolver import TenantChannelResolver


def build(settings: Settings, session_factory, tg_bot) -> AppContext:
    # --- core watcher ---
    monitored_repo = PgMonitoredDomainRepo(session_factory)
    idempotency = PgIdempotencyStore(session_factory)

    user_repo = PgUserRepo(session_factory)
    sub_repo = PgSubscriptionRepo(session_factory)

    tenant_resolver = TenantChannelResolver(sub_repo, user_repo)
    tenant_notifier = TenantTelegramNotifier(
        tg_bot=tg_bot,
        settings=settings.telegram,
    )

    watcher = (
        DomainWatcher.builder()
        .with_repository(monitored_repo)
        .with_idempotency_store(idempotency)
        .with_channel_resolver(tenant_resolver)
        .with_checker(RdapChecker())
        .with_checker(WhoisChecker())
        .with_notifier(tenant_notifier)
        .with_default_thresholds([Duration.days(30),
                                  Duration.days(7),
                                  Duration.days(1)])
        .build()
    )

    return AppContext(
        watcher=watcher,
        user_repo=user_repo,
        subscription_repo=sub_repo,
        tg_bot=tg_bot,
    )
```

### 4.1 Adding domains at runtime

The bot adds domains via `DomainWatcher.ensure_watching` — it does NOT use
the YAML file:

```python
class AddDomainUseCase:
    async def execute(self, telegram_id: int, raw_domain: str) -> Result:
        # ... user/quota checks elided ...
        domain = DomainName(raw_domain)

        # Subscription is bot-state.
        await self._subs.add(user.id, domain)

        # Tell core to start watching. Idempotent: same domain, same
        # checker is a no-op on the second call.
        await self._watcher.ensure_watching(
            domain,
            checker_id="rdap",
            channels=[],   # empty: resolver returns the actual channels
            metadata={"managed_by": "bot"},
        )
        return Result.ok()
```

Notes:
- `channels=[]` is intentional: the bot's `TenantChannelResolver` resolves
  dispatch-time channels from `subscriptions`, so the static
  `domain.channels` list stays empty. The dispatch use case allows empty
  static channels precisely when a non-static `ChannelResolver` is wired.
- `ensure_watching` upserts the row in `monitored_domain_state` and
  registers a scheduler job. Removing the last subscriber for a domain
  triggers `remove_watching` (no scheduler job, no active resolution).
- There is no YAML hot-reload path in bot mode. `ConfigFileWatcher` is
  constructed only by `DomainWatcher.from_config_file()`; the bot uses the
  builder.

The bot's lifecycle:

```python
async def main():
    ctx = build(...)
    async with ctx:
        await asyncio.gather(
            ctx.watcher.start(),       # scheduler + checks + notifications
            ctx.tg_bot.run(),          # handlers respond to user commands
        )
```

Both run in the same process by default. The watcher's
`TenantTelegramNotifier` and the bot's command handlers share the same
`Bot` instance.

## 5. Routing alerts to the right user

The bot routes alerts via a tenant-aware **ChannelResolver** (the port is
defined in core; see ADR 0002 §5). Routing data is **never** carried on
`MonitoredDomain.metadata` or `Alert.metadata` — those remain opaque
bookkeeping fields.

```python
class TenantChannelResolver(ChannelResolver):
    """One Channel per active subscriber. Returns no channels for blocked users."""

    def __init__(self, sub_repo: SubscriptionRepository, user_repo: UserRepository) -> None:
        ...

    async def channels_for(self, domain: MonitoredDomain) -> Sequence[Channel]:
        subs = await self._subs.subscribers_of(domain.name)
        out: list[Channel] = []
        for sub in subs:
            user = await self._users.get(sub.user_id)
            if user.is_blocked:
                continue
            out.append(
                Channel(
                    id=ChannelId(f"tg:{user.telegram_id}"),
                    notifier_id="telegram",
                    routing={"chat_id": str(user.telegram_id)},
                )
            )
        return out
```

The `TenantTelegramNotifier` becomes a thin reader of `channel.routing`:

```python
class TenantTelegramNotifier(Notifier):
    id = "telegram"

    def __init__(self, tg_bot, settings: TelegramSettings) -> None:
        self._bot = tg_bot
        self._parse_mode = settings.parse_mode
        # Bot token + transport defaults come from NotifierConfig.settings.
        # No subscriber lookup happens here.

    async def send(self, alert: Alert, channel: Channel) -> None:
        chat_id = channel.routing["chat_id"]
        try:
            await self._bot.send_message(
                chat_id=chat_id,
                text=self._format(alert),
                parse_mode=self._parse_mode,
            )
        except TelegramRetryAfter as e:
            raise DeliveryFailedError(...) from e
        except TelegramForbidden:
            # User blocked the bot. Mark and surface — the resolver will
            # exclude them on the next dispatch.
            await self._users.mark_blocked_by_chat_id(chat_id)
            raise NotificationError("user blocked the bot") from None
```

The dispatch use case (in `domain_watcher.application`) iterates
`alerts × resolver.channels_for(domain)`, checks IdempotencyStore per
(domain, threshold, cycle_id, channel_id), and calls `notifier.send`
for each survivor under `asyncio.gather(return_exceptions=True)` — a
permanent failure for one user does not cancel delivery to others.

## 6. The `PgMonitoredDomainRepo` adapter

This is where the bot adapts core's repository port to its multi-tenant
schema. The trick: from core's perspective there's still one
`MonitoredDomain` per `domain_name`. Subscriptions are bot-internal.

```python
class PgMonitoredDomainRepo(MonitoredDomainRepository):
    """Implements the core port over the bot's schema.

    Core sees a flat list of MonitoredDomains, one per FQDN. The repo
    collapses N subscribers into one row in monitored_domain_state.
    Subscriber-aware fan-out is performed by TenantChannelResolver at
    dispatch time, not by this repo (see §5).
    """

    async def list_all(self) -> Sequence[MonitoredDomain]:
        # SELECT DISTINCT domain_name FROM subscriptions
        # JOIN monitored_domain_state USING (domain_name)
        ...

    async def update(self, domain: MonitoredDomain) -> None:
        # UPSERT monitored_domain_state
        ...
```

The bot's `add_domain` use case is what writes to `subscriptions`; the
core repo is read-mostly from the watcher's perspective (writes are
limited to operational state).

## 7. Quota enforcement

Quota is bot-domain logic — core has no concept of users. Use case:

```python
class AddDomainUseCase:
    async def execute(self, telegram_id: int, raw_domain: str) -> Result:
        user = await self._users.get_or_create(telegram_id)
        if user.is_blocked:
            return Result.error("blocked")

        count = await self._subs.count_for(user.id)
        if count >= user.quota_max:
            return Result.error(f"quota {user.quota_max} reached")

        domain = DomainName(raw_domain)        # validates
        await self._subs.add(user.id, domain)

        # Tell core to start watching, idempotent on the domain side.
        await self._watcher.ensure_watching(
            domain,
            checker_id="rdap",
            channels=[],   # resolver returns dispatch-time channels
            metadata={"managed_by": "bot"},
        )
        return Result.ok()
```

## 8. Why embedded (not HTTP) by default

| Concern              | Embedded                         | HTTP                                        |
| -------------------- | -------------------------------- | ------------------------------------------- |
| Latency              | function call                    | localhost ~ms; +serialization                |
| Failure modes        | one process — clear              | network partition between bot & watcher     |
| Ops                  | one container                    | two services, two deployments               |
| Backpressure         | natural via asyncio              | needs explicit handling                     |
| Horizontal scaling   | scale the whole bot              | scale watcher independently                 |

For the expected scale of a free-tier multi-tenant bot (≤10⁴ users,
≤10⁵ domains), embedded wins on every axis except horizontal scaling,
which we do not need yet. If we need it later, the public library API
already returns `async for` events; an HTTP shim is straightforward to
add.

## 9. Concurrency and failure isolation

- The watcher runs scheduler-owned tasks. A task crash is isolated to that
  domain's check; the scheduler logs and reschedules.
- Notification dispatch uses `asyncio.gather(return_exceptions=True)`.
  Each per-channel coroutine catches `DeliveryFailedError` and emits a
  failure event, so one user's failure does not cancel delivery to others.
- The bot's command handlers run in the aiogram/python-telegram-bot
  event loop; they share the asyncio loop with the watcher.
- Postgres connections are pooled (`asyncpg` pool). Watcher writes to
  `monitored_domain_state` and `alert_idempotency`; bot writes to
  `users` and `subscriptions`. No write conflicts by design.

## 10. Ops surface (bot repo)

- `docker compose up` — bot + Postgres + (optional) Ollama as the default
  local LiteLLM backend; swap via `parsing.llm_fallback.suggester.settings.model`.
- Healthcheck: HTTP `/healthz` on the bot process exposes
  `{watcher_ok, tg_ok, db_ok}`.
- Metrics: Prometheus `/metrics` (counter: alerts_sent_total{channel,severity};
  gauge: monitored_domains; histogram: check_duration_seconds).
- Logs: structlog → JSON → stdout.
- Migrations: `alembic upgrade head` on container start (idempotent).

## 11. What the bot **must not** do

- Reach into `domain_watcher.core` or `domain_watcher.infrastructure`.
  Use the public surface only.
- Reimplement RDAP/WHOIS. If a TLD is unsupported, file an issue or
  contribute a parse rule via `suggest-rule`.
- Bypass the `IdempotencyStore`. Sending the same threshold alert twice
  on a single domain crossing is a bug, not a feature.
- Persist `Alert` objects directly. Alerts are ephemeral; the bot logs
  them but does not own their lifecycle.
- Use `MonitoredDomain.metadata` or `Alert.metadata` for tenant routing.
  Routing is the resolver's job; metadata is opaque bookkeeping.
