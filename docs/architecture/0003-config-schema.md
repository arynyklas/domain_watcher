# ADR 0003 — Configuration Schema & Hot Reload

> Status: **DRAFT**, awaiting approval.
> Related: [0001 Overview](./0001-overview.md), [0002 Bounded Contexts](./0002-bounded-contexts.md).

## 1. Source of truth (recommended)

For the standalone Docker app:

- **YAML file** is the source of truth for the static topology:
  domains, channels, parse-rules, schedules.
- **Database** holds operational state only:
  `last_check`, idempotency keys, retry counters, in-flight job markers.

Why: configuration is a versioned artifact reviewed in PRs. Splitting
"what the operator decided" from "what the system observed" lets us
restart with a clean DB and recover state from sources, but never lose
the operator's intent to a corrupt schema migration.

This is reversible. The bot repo overrides this — its source of truth is
its Postgres tables, because end users add domains via Telegram, not via
file edits. See [ADR 0005](./0005-bot-integration.md).

## 2. File location & precedence

Resolution order (first match wins):

1. `--config <path>` CLI flag
2. `DOMAIN_WATCHER_CONFIG` env var
3. `./domain-watcher.yaml`
4. `/etc/domain-watcher/config.yaml`
5. `$XDG_CONFIG_HOME/domain-watcher/config.yaml`

Environment-variable interpolation uses `${NAME}` and `${NAME:-default}`
syntax. Interpolation happens **after** YAML parse, before Pydantic
validation. Missing required vars fail loudly at startup.

## 3. Top-level schema

```yaml
# domain-watcher.yaml
version: 1                            # required; lets us evolve the schema

# Operational settings
runtime:
  log_level: INFO                     # DEBUG|INFO|WARNING|ERROR
  log_format: json                    # json|console
  timezone: UTC                       # used only for cron interpretation
  state_db: sqlite:///state.db        # for last_check, idempotency

# Plugin / adapter selection
checkers:                             # which strategies are available
  - id: rdap
    type: rdap
    settings:
      timeout: 10s
      bootstrap_url: https://data.iana.org/rdap/dns.json
  - id: whois
    type: whois
    settings:
      timeout: 10s
      port: 43
  - id: my-script
    type: script
    settings:
      command: ["./scripts/check.sh"]
      timeout: 30s

# Notifiers — keyed by id, referenced from `domains`
notifiers:
  - id: tg-ops
    type: telegram
    settings:
      bot_token: ${TG_BOT_TOKEN}
      chat_id: ${TG_OPS_CHAT}
  - id: email-team
    type: email
    settings:
      smtp_host: smtp.example.com
      smtp_port: 587
      username: alerts@example.com
      password: ${SMTP_PASSWORD}
      from_addr: "Domain Watcher <alerts@example.com>"
      to_addrs: ["ops@example.com"]
      use_starttls: true
  - id: discord-eng
    type: discord
    settings:
      webhook_url: ${DISCORD_WEBHOOK}
  - id: pagerduty
    type: webhook
    settings:
      url: https://events.pagerduty.com/...
      method: POST
      headers:
        Authorization: "Token ${PD_TOKEN}"
      body_template: |
        {"summary": "${domain} expires in ${threshold}"}

# Notification policy — defaults applied to every domain unless overridden
notification_defaults:
  thresholds: ["30d", "14d", "7d", "1d"]
  retry:
    max_attempts: 3
    base_delay: 1s
    factor: 5.0

# WHOIS parse rules — referenced when checker == whois
parsing:
  whois_rules:
    - tld: ru
      expires_regex: 'paid-till:\s+(\S+)'
      date_format: iso8601
    - tld: com
      expires_regex: 'Registry Expiry Date:\s+(\S+)'
      date_format: iso8601
    - tld: co.uk
      expires_regex: 'Expiry date:\s+(\d{2}-\w{3}-\d{4})'
      date_format: dd-mmm-yyyy
      timezone: Europe/London
  llm_fallback:
    enabled: true                     # if false, ParseFailed when no rule matches
    suggester:
      type: litellm
      settings:
        model: ollama/gemma3              # any LiteLLM model id: "<provider>/<model>"
        api_base: http://localhost:11434  # optional; for self-hosted backends (Ollama, vLLM)
        api_key: ${LLM_API_KEY:-}         # optional; for cloud providers (OpenAI, Anthropic)
        timeout: 30s
        temperature: 0                    # determinism: 0 always
    safety:
      max_age_years: 50               # reject suggested dates >50y in future
      min_age_seconds: 0              # reject dates in the past
      validate_on_store: true         # always; non-overridable in v1
      revalidate_after: "30d"         # health-check learned rules monthly
      max_learn_per_hour: 5               # rate-limit LLM calls per host
  # Learned rules live in the state DB (see runtime.state_db).
  # Inspect or promote them with:  domain-watcher rules learned [--promote]

# Domains — the actual watchlist
domains:
  - name: example.com
    checker: rdap                     # references checkers[].id
    schedule: "0 */6 * * *"           # cron, in runtime.timezone
    channels: [tg-ops, email-team]    # references notifiers[].id
    # optional per-domain overrides:
    thresholds: ["60d", "30d", "7d", "1d"]
    metadata:
      owner: platform-team
  - name: legacy.local
    checker: whois
    schedule: "0 */12 * * *"
    channels: [discord-eng]
```

> **NotifierConfig.settings vs Channel.routing.** The `settings:` block above is *construction-time configuration* — secrets (bot tokens, SMTP passwords), connection parameters (smtp_host, port), and transport defaults. It is loaded once at startup and rebound on hot reload (ADR 0003 §5). Per-recipient *addressing* data (e.g. an individual Telegram chat_id) lives on `Channel.routing` (see [ADR 0002 §5](./0002-bounded-contexts.md#5-corenotification--when-and-how-to-alert)). Routing is opaque to the YAML schema because it is dynamic — the standalone app uses static `domain.channels` mapped 1:1, but the bot generates routing per active subscriber. Secrets MUST NOT appear in routing; routing is logged for diagnostics, settings are not.

### Webhook body templates

Webhook bodies are rendered with `string.Template` (`$var` / `${var}`). Supported placeholders:

| Placeholder     | Meaning                                                     |
| --------------- | ----------------------------------------------------------- |
| `${domain}`     | FQDN of the expiring domain                                 |
| `${expires_at}` | RFC 3339 expiration timestamp (UTC)                         |
| `${threshold}`  | Duration string (e.g. `7d`)                                 |
| `${severity}`   | One of `info`, `warning`, `critical`                        |
| `${cycle_id}`   | Stable hash of expires_at; useful for deduping in receivers |

Unknown placeholders raise `ConfigError` at startup (eager validation). Templates that compile but reference non-existent fields are a startup error, not a runtime surprise.

## 4. Pydantic schema (skeleton)

```python
# infrastructure/config/schema.py
class CheckerConfig(BaseModel):
    id: str
    type: Literal["rdap", "whois", "script"] | str   # plugins extend this
    settings: dict[str, Any] = {}

class NotifierConfig(BaseModel):
    id: str
    type: str
    settings: dict[str, Any] = {}

class WhoisRule(BaseModel):
    tld: str
    expires_regex: str
    date_format: DateFormat
    timezone: str = "UTC"
    strptime_format: str | None = None

    @field_validator("expires_regex")
    @classmethod
    def _exactly_one_group(cls, v: str) -> str: ...

class DomainEntry(BaseModel):
    name: str
    checker: str
    schedule: str
    channels: list[str]
    thresholds: list[Duration] | None = None
    metadata: dict[str, str] = {}

class Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    runtime: RuntimeConfig
    checkers: list[CheckerConfig]
    notifiers: list[NotifierConfig]
    notification_defaults: NotificationDefaults
    parsing: ParsingConfig
    domains: list[DomainEntry]

    @model_validator(mode="after")
    def _references_resolve(self) -> "Config":
        # Every domain.checker must exist in checkers[].id
        # Every domain.channels[i] must exist in notifiers[].id
        # Every whois_rule.tld is unique
        ...
```

`Config` is **frozen**. Mutation produces a new instance via
`Config.model_copy(update=...)`.

## 5. Hot reload mechanics

```python
# application/use_cases/reload_config.py

class ConfigHolder:
    def __init__(self, initial: Config) -> None:
        self._current = initial
        self._lock = asyncio.Lock()
        self._subscribers: list[Subscriber] = []

    def current(self) -> Config:
        return self._current            # atomic; reading a frozen ref

    async def update(self, new: Config) -> None:
        async with self._lock:
            old = self._current
            self._current = new
        for sub in self._subscribers:
            try:
                await sub.on_config_changed(old, new)
            except Exception:
                logger.exception("subscriber failed during reload")
        # ^ subscriber failures must not abort the reload
```

Subscribers (each in `application/`):

| Subscriber                     | Reaction                                            |
| ------------------------------ | --------------------------------------------------- |
| `SchedulerService`             | Reconcile job set: add new, remove gone, leave rest |
| `CheckerRegistry`              | Re-instantiate checker adapters whose settings changed |
| `NotifierRegistry`             | Same, for notifiers                                 |
| `ParsingService`               | Replace whois rules table                           |

```python
# infrastructure/config/watcher.py

class ConfigFileWatcher:
    def __init__(
        self, path: Path, loader: ConfigLoader, holder: ConfigHolder
    ) -> None: ...

    async def start(self) -> None:
        # watchdog.Observer fires on close_write/move; debounce 200ms
        # then: loader.load(path) → validate → holder.update(new)
        # on validation failure: log error, keep old config
        ...
```

### Reconciliation rules

Reconciliation operates on **diffs**, not full restarts:

- A domain whose `(name, checker, schedule, channels, thresholds)` is
  unchanged keeps its scheduled job.
- Adding a domain → schedule a new job.
- Removing a domain → cancel its job; **do not** delete its operational
  state from the DB (we keep idempotency history).
- Changing a notifier's settings → drain in-flight deliveries with the old
  notifier, then swap.
- A notifier whose id is removed from YAML is **drained, then dropped**:
  in-flight `Notifier.send()` calls complete with the old instance; new
  dispatches to that id raise `PluginNotFoundError`. The registry update
  is atomic relative to dispatch (see ADR 0002 §6 EventPublisher contract).

This avoids the "every save retriggers all checks" footgun.

## 6. Validation rules (Pydantic + custom)

| Rule                                                          | Why                                |
| ------------------------------------------------------------- | ---------------------------------- |
| `version == 1`                                                | future-proofing                    |
| `domains[*].checker` exists in `checkers[*].id`               | reference integrity                |
| `domains[*].channels[*]` all exist in `notifiers[*].id`       | reference integrity                |
| `domains[*].schedule` is a valid 5-field cron                 | early failure                      |
| `domains[*].thresholds` strictly descending, non-empty        | policy invariant                   |
| `parsing.whois_rules[*].expires_regex` has exactly 1 group    | parser invariant                   |
| `notifiers[*].id` and `checkers[*].id` are unique             | identity                           |
| All env-var references resolve                                | fail loudly at startup, not later  |

A failed reload **never** crashes the daemon. A failed initial load **does**
crash the daemon, with a non-zero exit code and a structured error report.

## 7. Secrets

Secrets enter through env vars, never through the YAML file. The YAML
contains references like `${SMTP_PASSWORD}`, expanded during loading.
A future iteration may add a `secret-source` plugin (Vault, AWS SM)
behind a `SecretSource` port, but v1 stays env-only.

Logs **never** print resolved secret values. Pydantic's `SecretStr` is
used for credential fields.

At log-emission time, structlog runs a `scrub_secrets` processor (configured
in `infrastructure/observability/structlog_setup.py`). It redacts these keys
to `"***"` (case-insensitive match): `bot_token`, `password`, `api_key`,
`smtp_password`, `secret`, `token`, `authorization`. URL fields
(`webhook_url`, `api_base`) are normalized to `scheme://host` — userinfo and
query strings are dropped. The scrubber is **on by default**; disabling it in
production is a configuration error and emits a startup warning.

## 8. Worked example — operational flow

```
$ vim /etc/domain-watcher/config.yaml          # add a new domain, save
[INFO] config.watcher: change detected, reloading
[INFO] config.loader: parsed; validation passed
[INFO] scheduler: +1 job (newdomain.com), -0 jobs, 14 unchanged
[INFO] config.holder: applied version=1
```

```
$ vim /etc/domain-watcher/config.yaml          # introduce a typo
[ERROR] config.loader: validation failed: domains[3].checker references
        unknown id 'rdao'; keeping previous config (loaded 14m ago)
```

The system keeps running on the previous good config until the operator
fixes the typo and saves again.
