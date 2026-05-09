# ADR 0004 — Plugin Protocol

> Status: **DRAFT**, awaiting approval.
> Related: [0001 Overview](./0001-overview.md),
> [0002 Bounded Contexts](./0002-bounded-contexts.md).

This ADR defines the contract a third-party package must satisfy to add
new checkers, notifiers, parsers, or rule suggesters to the system.

## 1. Goals

- Add new strategies without forking the core.
- Keep the public surface small and typed.
- Make plugin discovery work for both ad-hoc tests (explicit registration)
  and production deployments (entry points).

## 2. Non-goals

- Cross-process plugins.
- A general lifecycle framework (start/stop/health). Plugins implement a
  single async method; that is the framework.

## 3. Three plugin kinds

| Kind            | Port (`core/`)        | Registration group                |
| --------------- | --------------------- | --------------------------------- |
| `Checker`       | `ExpirationChecker`   | `domain_watcher.checkers`         |
| `Notifier`      | `Notifier`            | `domain_watcher.notifiers`        |
| `Parser`        | `WhoisParser`         | `domain_watcher.parsers`          |
| `RuleSuggester` | `RuleSuggester`       | `domain_watcher.rule_suggesters`  |

All four kinds run **at runtime**. `RuleSuggester` is special: it is invoked
by the parser only when no static or learned rule matches. Its output is
always self-validated and persisted in the `learned_rules` state table —
see [ADR 0006](./0006-runtime-llm-fallback.md) for the full safety contract.

## 4. Port contracts

### 4.1 Checker

```python
class ExpirationChecker(Protocol):
    id: ClassVar[str]                  # unique across loaded plugins

    async def check(self, domain: DomainName) -> CheckResult: ...
```

Contract:

- Must be safe to call concurrently for different domains.
- Must classify failures: any non-`OK` `CheckResult` carries either
  `TRANSIENT_ERROR` (the orchestrator will retry) or `PERMANENT_ERROR`
  (no retry; emit `DomainCheckFailed`).
- Must complete within the configured `timeout` or raise
  `asyncio.TimeoutError`. The orchestrator will treat that as transient.
- Must not perform notification side effects.

### 4.2 Notifier

```python
class Notifier(Protocol):
    id: ClassVar[str]

    async def send(self, alert: Alert, channel: Channel) -> None: ...
    # ^ raises DeliveryFailedError on transport failure (retryable)
    # ^ raises NotificationError on permanent failure (e.g. invalid token)
```

Contract:

- **At-least-once delivery.** The transport may retry on transient failure; receivers MUST tolerate duplicate alerts. Notifier authors MUST NOT track 'already sent' state — that is the IdempotencyStore's job, recorded by the dispatch use case only after `send()` returns successfully. A notifier that internally dedups will hide failures from the orchestrator and break the (domain, threshold, cycle_id, channel) idempotency model defined in ADR 0002 §5.
- `channel.routing` is opaque to core and varies per call (see ADR 0002 §5). The notifier validates its shape on first send (lazy) — routing values like `chat_id` come from a resolver, not from YAML. Construction-time configuration (transport secrets, default endpoints) is supplied through the factory's `settings: Mapping[str, Any]` argument and validated eagerly at construction.
- Must use no global state. The factory receives all configuration.

### 4.3 Parser

```python
class WhoisParser(Protocol):
    async def parse(
        self, raw: str, domain: DomainName, rules: Sequence[ParseRule]
    ) -> datetime: ...     # tz-aware UTC; raises NoMatchingRuleError
```

The default `regex` parser ships with core. A plugin may replace it for
exotic registries (e.g., binary formats), but most users will not.

### 4.4 RuleSuggester (runtime)

```python
class RuleSuggester(Protocol):
    id: ClassVar[str]

    async def suggest(
        self, raw_whois: str, domain: DomainName
    ) -> ParseRule: ...
    # ^ raises SuggestionError on transport / model failure (treated as transient)
```

Contract — every implementation MUST:

- Return a `ParseRule` whose `expires_regex` has exactly one capture group.
- Be deterministic at temperature 0 (or as close as the backend allows);
  callers can rely on identical input ⇒ identical output for cacheability.
- Complete within the configured timeout. Slow backends do not block the
  parser indefinitely.
- Not access state stores. The parser caller is responsible for
  self-validation and persistence.

Built-in backend: **LiteLLM** (`type: litellm`) — one adapter that brokers
~100 providers (Ollama, OpenAI, Anthropic, Azure, vLLM, …) via a single
`model: <provider>/<model>` config string. The default model is
`ollama/gemma3` for fully-local self-host. A plugin can ship a more
specialized backend by satisfying this protocol directly.

The caller (the parser orchestrator in `application/`) is responsible for:

- Self-validating the suggested rule against the same WHOIS text.
- Range-checking the resulting date.
- Persisting accepted rules to the `learned_rules` table.
- Rate-limiting calls per `parsing.llm_fallback.safety.max_learn_per_hour` and `safety.max_learn_per_tld_per_24h` (the second is a new ceiling — per-TLD, see ADR 0006 §7).

The `RuleSuggester` plugin itself is wrapped at the infrastructure boundary by a `SuggesterCircuitBreaker` (transport-health concern) which trips after 5 consecutive `SuggestionError`s in 5 minutes and stays open for 5 minutes. This wrapper does NOT enforce rate limits — those are policy decisions owned by `ParsingService`. The two concerns are deliberately separated: rate limit changes via config; circuit breaker tuning is internal.

All of that lives outside the plugin so authors of new backends cannot
accidentally weaken the safety rails. See [ADR 0006](./0006-runtime-llm-fallback.md).

## 5. Registration paths

### 5.1 Explicit (always available)

```python
from domain_watcher import DomainWatcher
from my_pkg import S3WebhookNotifier

watcher = DomainWatcher.builder() \
    .with_notifier(S3WebhookNotifier(bucket="alerts")) \
    .build()
```

`with_checker`, `with_notifier`, `with_parser`, `with_rule_suggester`
all take an instance, read `.id` from it, and add it to the typed
registry. Duplicate ids raise `PluginConflictError`.

### 5.2 Entry points (auto-discovered)

```toml
# my_pkg/pyproject.toml
[project.entry-points."domain_watcher.notifiers"]
s3-webhook = "my_pkg.notifiers:S3WebhookNotifier"
```

When `DomainWatcher.from_config_file(...)` is used (the standard
standalone path), the composition step calls `entry_points(group=...)`
for each of the four groups and registers everything found. Entry-point
classes must:

- Have a no-arg or `(settings: Mapping[str, Any])` constructor.
- Expose `.id` as a classvar.

A plugin can opt out of auto-discovery by leaving its entry point unset
and forcing explicit wiring.

### 5.3 Disabling plugins

```yaml
runtime:
  plugins:
    enabled: ["rdap", "whois", "telegram", "regex"]   # whitelist
    # or:
    # disabled: ["script"]                             # blacklist
```

If neither is set, all discovered plugins are loaded.

## 6. Built-in plugins

| Group           | id           | Module                                                |
| --------------- | ------------ | ----------------------------------------------------- |
| checkers        | `rdap`       | `domain_watcher.infrastructure.checkers.rdap`         |
| checkers        | `whois`      | `domain_watcher.infrastructure.checkers.whois`        |
| checkers        | `script`     | `domain_watcher.infrastructure.checkers.script`       |
| notifiers       | `telegram`   | `domain_watcher.infrastructure.notifiers.telegram`    |
| notifiers       | `email`      | `domain_watcher.infrastructure.notifiers.email_smtp`  |
| notifiers       | `discord`    | `domain_watcher.infrastructure.notifiers.discord`     |
| notifiers       | `webhook`    | `domain_watcher.infrastructure.notifiers.webhook`     |
| parsers         | `regex`      | `domain_watcher.infrastructure.parsers.regex`         |
| rule_suggesters | `litellm`    | `domain_watcher.infrastructure.parsers.llm_suggester` |

## 7. The `script` checker — interface contract

The `script` checker is a generic escape hatch. It runs a user-provided
binary or shell script and reads JSON from stdout.

```bash
$ ./check.sh example.com
{"outcome": "ok", "expires_at": "2027-12-31T00:00:00Z", "raw": "..."}
```

Schema:

```json
{
  "outcome": "ok" | "transient_error" | "permanent_error",
  "expires_at": "<RFC 3339>",          // required iff outcome == ok
  "error": "<string>",                  // required iff outcome != ok
  "raw": "<string>"                     // optional; for audit
}
```

Stdin is closed. Argv is `[command..., domain_name]`. Non-JSON stdout
or non-zero exit with non-error outcome is a contract violation and
yields `PERMANENT_ERROR` with the captured stderr (truncated to 4 KiB).

## 8. Validation & loading order

```
1. Read config.yaml
2. Load entry points for each group
3. Apply plugins.enabled / .disabled filters
4. For each `checkers[]` / `notifiers[]` entry:
     find type → factory → instantiate with settings
     register under entry.id
5. Validate references (every domain.checker / .channels exists)
6. Build the application context
```

A plugin failing to instantiate (e.g., missing dep, bad settings) is a
fatal startup error — we refuse to come up partially. On hot reload,
a plugin failure keeps the previous state and logs the error.

## 9. Versioning the plugin protocol

The plugin protocol is versioned independently from the application:

```python
# domain_watcher/plugins/__init__.py
PLUGIN_PROTOCOL_VERSION = 1
```

Plugins declare:

```toml
[project.entry-points."domain_watcher.metadata"]
protocol_version = "my_pkg:PROTOCOL_VERSION"
```

If a plugin's declared version is incompatible with the host, loading is
refused with a clear error. v1 → v2 will be a breaking change worth
calling out.

## 10. Testing a plugin

A plugin author gets a stable test harness:

```python
from domain_watcher.testing import (
    PluginContractTest,
    fake_clock,
    in_memory_repo,
)

class TestS3Notifier(PluginContractTest):
    notifier_factory = lambda self: S3WebhookNotifier(...)
```

`PluginContractTest` runs a battery of conformance tests:

- "send raises `DeliveryFailedError` when transport is down"
- "send is at-least-once: a transient failure followed by a retry does NOT raise from the notifier itself (transport handles retry), and the orchestrator's IdempotencyStore is the dedup boundary"
- "constructor validates settings eagerly"

Plugins that pass these tests are guaranteed to integrate correctly.
