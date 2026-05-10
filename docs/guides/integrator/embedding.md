# Embedding `domain_watcher` in another async app

`domain_watcher` ships both as a standalone daemon (the Docker image)
and as a Python library. The library mode is what the Telegram-bot
repo and any other async host imports. This guide shows how to wire the
façade without YAML so an embedding application can keep its own
configuration story.

> The bot repository itself is **out of scope** here (ADR 0005). What
> follows is the contract the bot — and any other integrator — depends
> on.

## Public surface

```python
from domain_watcher import (
    DomainWatcher, DomainWatcherBuilder,
    DomainName, Duration,
    CheckResult, CheckOutcome,
    Alert, AlertSeverity,
    DomainCheckCompleted, DomainCheckFailed,
    NotificationDispatched, NotificationFailed,
    WhoisRuleLearned,
    ParseFailed,
)
from domain_watcher.adapters import (
    RdapChecker, WhoisChecker, ScriptChecker,
    TelegramNotifier, EmailNotifier, DiscordNotifier, WebhookNotifier,
    RegexWhoisParser, LiteLLMRuleSuggester,
    MemoryMonitoredDomainRepo, SqlMonitoredDomainRepo,
)
from domain_watcher.testing import FixedClock, MemoryIdempotencyStore
```

Anything not re-exported from `domain_watcher` or
`domain_watcher.adapters` is private and may move between minor
releases. `domain_watcher.testing` is a stable public surface for
plugin authors and integrators that need deterministic clocks or memory
repos.

## Builder mode

```python
import asyncio

from domain_watcher import DomainWatcher, DomainName, Duration
from domain_watcher.adapters import (
    RdapChecker, MemoryMonitoredDomainRepo,
)
from your_app.notifiers import BotChannelNotifier
from your_app.routing import BotChannelResolver


async def main() -> None:
    watcher = (
        DomainWatcher.builder()
        .with_repo(MemoryMonitoredDomainRepo())
        .with_checker(RdapChecker(timeout=10.0))
        .with_notifier(BotChannelNotifier(...))
        .with_channel_resolver(BotChannelResolver(...))
        .with_default_thresholds(
            (Duration.days(30), Duration.days(7), Duration.days(1))
        )
        .build()
    )

    # Subscribe before start so you don't miss bootstrap events.
    @watcher.on(DomainCheckCompleted)
    async def on_completed(evt):
        ...

    await watcher.start()
    try:
        await watcher.ensure_watching(
            DomainName("example.com"),
            checker_id="rdap",
            channels=["bot:user-12345"],
        )
        await asyncio.Event().wait()  # run until something cancels you
    finally:
        await watcher.stop()


asyncio.run(main())
```

`builder()` validates the registries on `.build()` — missing checker,
missing notifier, missing channel resolver all raise immediately.

## Channel resolution

The default `StaticChannelResolver` wired by `compose_from_config` maps
a `ChannelId` to a `notifier_id` 1:1 — fine for a daemon with N fixed
notifier instances. For a bot, `domain.channels` typically references a
*subscriber id* and the resolver fans out to all notifier transports a
subscriber wants. Implement `ChannelResolver`:

```python
class BotChannelResolver:
    async def channels_for(self, domain: MonitoredDomain) -> Sequence[Channel]:
        # domain.channels is whatever your domain holds — e.g. a
        # subscriber id. Look up that subscriber's preferred transports
        # and emit one Channel per transport.
        return [
            Channel(id=ChannelId(f"bot:tg:{user_id}"), notifier_id="telegram"),
            Channel(id=ChannelId(f"bot:email:{user_id}"), notifier_id="email"),
        ]
```

Idempotency keys are still `(domain, threshold, cycle_id, channel_id)`,
so a subscriber added mid-cycle does not get back-paged.

## Lifecycle

- `start()` is idempotent. It seeds the repository with
  `initial_domains` if any were registered, runs `start_hooks` (used by
  the `/metrics` listener), and starts the scheduler.
- `stop()` is idempotent. It stops the scheduler and runs every
  `aclose_hook`.
- `ensure_watching` is idempotent: re-calling with identical args is a
  no-op; calling with a new schedule re-schedules in place; `metadata`
  changes are persisted.
- `remove_watching` removes the scheduler job and the repository row
  but leaves `alert_idempotency` rows intact so a re-add inside the
  same cycle does not re-fire.

## Events

The bus is in-process. Subscribe via `on(event_type, handler)` for a
typed callback or `on_any(handler)` for everything. Every callback is
async and exception-isolated — a raising handler does not block other
handlers on the same event.

The same events are also reachable as an async iterator
(`watcher.events()`) for code that prefers a pull model. ADR 0001
§11(2) explains why both APIs exist.

## State and durability

The library does NOT pick persistence for you:

- For a long-lived process, build with `SqlMonitoredDomainRepo` against
  Postgres or SQLite (use `compose_from_config` for the wiring, or
  the SQL UoW directly).
- For a short-lived process, `MemoryMonitoredDomainRepo` is fine but
  understand that lost idempotency state means re-pages on restart.

The bot repo, per ADR 0005, owns Postgres in its own way and supplies
its repos to the builder. The library never opens connections it was
not handed.

## Testing your integration

```python
from domain_watcher.testing import (
    FixedClock,
    MemoryMonitoredDomainRepo,
    MemoryIdempotencyStore,
    PluginContractTest,
)
```

The contract harnesses (`PluginContractTest`, `CheckerContractTest`,
`RepoContractTest`) are the executable definition of the plugin
contracts — wire your custom adapter into one and pytest will tell you
exactly what your implementation got wrong.
