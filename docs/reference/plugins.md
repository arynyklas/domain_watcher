# Plugin protocol

`domain-watcher` discovers third-party adapters through Python entry
points. Every plugin kind has a stable `Protocol` it must implement and
a contract test fixture that the host runs against the implementation
during CI.

## Entry-point groups

| Plugin kind       | Entry-point group                     | Built-in `type` ids |
| ----------------- | ------------------------------------- | ------------------- |
| Checker           | `domain_watcher.checkers`             | `rdap`, `whois`, `script` |
| Notifier          | `domain_watcher.notifiers`            | `telegram`, `email`, `discord`, `webhook` |
| Parser            | `domain_watcher.parsers`              | `regex` |
| Rule suggester    | `domain_watcher.rule_suggesters`      | `litellm` |

A package registers a plugin in its own `pyproject.toml`:

```toml
[project.entry-points."domain_watcher.checkers"]
my-checker = "my_pkg.adapters:MyChecker"
```

The host calls `importlib.metadata.entry_points` at composition time,
imports the class lazily, and instantiates it from the YAML
`settings:` block.

## Allow- / deny-lists

`runtime.plugins` lets operators control which discovered plugins are
permitted:

```yaml
runtime:
  plugins:
    enabled: [rdap, telegram, my-checker]   # allowlist; non-empty wins
    disabled: [discord]                     # denylist; ignored when enabled is set
```

Unknown ids fail startup with a `ConfigError`. Plugins refusing to load
because of a protocol-version mismatch fail startup loudly — there is
no silent fall-through.

## Conformance harnesses

`domain_watcher.testing` ships pytest base classes that exercise the
contract every implementation must satisfy:

- `PluginContractTest` — base, runs structural assertions for any
  plugin kind.
- `CheckerContractTest` — outcome envelope, retry classification,
  timeout behaviour.
- `RepoContractTest` — idempotency semantics for monitored-domain
  repos.

Wire your adapter into the relevant harness and pytest will surface
any contract violation:

```python
from domain_watcher.testing import CheckerContractTest

class TestMyChecker(CheckerContractTest):
    @pytest.fixture
    def checker(self):
        return MyChecker(timeout=5.0)
```

## Designing a checker

A checker maps a `DomainName` to a `CheckResult`. Implementations:

- MUST return a `CheckResult` for every input — never raise out of
  `check()`.
- MUST classify failures as `CheckOutcome.TRANSIENT` (retry) or
  `CheckOutcome.PERMANENT` (give up this cycle, raise `Alert` if
  thresholds were crossed last time).
- SHOULD honour the `timeout` setting passed by the host; the host
  wraps every call in `asyncio.wait_for` regardless, but a cooperative
  checker yields cleaner cancellations.

## Designing a notifier

A notifier sends a rendered `Alert` to a single channel. Implementations:

- MUST be idempotent against `(domain, threshold, cycle_id, channel_id)`
  — the host de-duplicates, but a notifier that POSTs the same payload
  twice still sends two messages from the registry's perspective. Treat
  duplicates as no-ops.
- MUST surface transient transport errors as `NotificationError`
  subclasses; the host retries with exponential backoff governed by
  `notification_defaults.retry`.

## Designing a parser

A parser converts raw WHOIS text into a `(expires_at, registrar?)`
tuple. The static built-in (`RegexWhoisParser`) is sufficient for
known-format TLDs. The runtime LLM-fallback path is opt-in
(`parsing.llm_fallback.enabled: true`) and gates every learned rule
through the safety pipeline documented in
[Learned WHOIS rules](../guides/operator/learned-rules.md).
