# Python API reference

The public surface of `domain_watcher` is everything re-exported from
the top-level package and from `domain_watcher.adapters` /
`domain_watcher.testing`. Anything imported from a deeper path is
private and may move between minor releases.

## Library façade

```{eval-rst}
.. autosummary::
   :toctree: _generated
   :nosignatures:

   domain_watcher.DomainWatcher
   domain_watcher.DomainWatcherBuilder
```

## Value objects and entities

```{eval-rst}
.. autosummary::
   :toctree: _generated
   :nosignatures:

   domain_watcher.DomainName
   domain_watcher.Duration
   domain_watcher.ChannelId
   domain_watcher.CheckSchedule
   domain_watcher.CheckOutcome
   domain_watcher.CheckResult
   domain_watcher.Alert
   domain_watcher.AlertSeverity
```

## Events

```{eval-rst}
.. autosummary::
   :toctree: _generated
   :nosignatures:

   domain_watcher.DomainEvent
   domain_watcher.DomainCheckCompleted
   domain_watcher.DomainCheckFailed
   domain_watcher.NotificationDispatched
   domain_watcher.NotificationFailed
   domain_watcher.WhoisRuleLearned
   domain_watcher.WhoisRuleRevalidated
   domain_watcher.WhoisRuleInvalidated
   domain_watcher.ParseFailed
```

## Errors

```{eval-rst}
.. autosummary::
   :toctree: _generated
   :nosignatures:

   domain_watcher.DomainWatcherError
   domain_watcher.ConfigError
   domain_watcher.NotificationError
   domain_watcher.ParseError
```

## Adapters

```{eval-rst}
.. autosummary::
   :toctree: _generated
   :nosignatures:

   domain_watcher.adapters.RdapChecker
   domain_watcher.adapters.WhoisChecker
   domain_watcher.adapters.ScriptChecker
   domain_watcher.adapters.TelegramNotifier
   domain_watcher.adapters.EmailNotifier
   domain_watcher.adapters.DiscordNotifier
   domain_watcher.adapters.WebhookNotifier
   domain_watcher.adapters.RegexWhoisParser
   domain_watcher.adapters.LiteLLMRuleSuggester
   domain_watcher.adapters.MemoryMonitoredDomainRepo
   domain_watcher.adapters.MemoryLearnedRulesRepo
   domain_watcher.adapters.MemoryIdempotencyStore
   domain_watcher.adapters.SqlMonitoredDomainRepo
   domain_watcher.adapters.SqlLearnedRulesRepo
   domain_watcher.adapters.SqlIdempotencyStore
   domain_watcher.adapters.ApsScheduler
```

## Test helpers

`domain_watcher.testing` is a stable public surface for plugin authors
and integrators that need deterministic clocks, in-memory repositories,
or the protocol conformance harnesses. The repos and idempotency store
are the same classes re-exported from `domain_watcher.adapters` above —
only `FixedClock` is unique to this module.

```{eval-rst}
.. autosummary::
   :toctree: _generated
   :nosignatures:

   domain_watcher.testing.FixedClock
```
