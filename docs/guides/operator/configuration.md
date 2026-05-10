# Configuration reference

The YAML schema is the single source of truth at startup and on hot
reload. Every field is validated eagerly: typos in cross-references,
descending-thresholds violations, malformed crons, regexes without a
capture group, and unknown webhook placeholders all surface here —
never at runtime. The Pydantic schema lives at
`src/domain_watcher/infrastructure/config/schema.py`; this guide
explains the shape with operator-level intent.

For environment-variable interpolation rules see
[ADR 0003 §3](../../architecture/0003-config-schema.md). Defaults are
listed only when the field has one.

## Top level

```yaml
version: 1
runtime: { ... }
checkers: [ ... ]
notifiers: [ ... ]
notification_defaults: { ... }
parsing: { ... }
domains: [ ... ]
```

`version` MUST be `1`. Bumping it is a breaking change worth a release
note.

## `runtime`

```yaml
runtime:
  log_level: INFO                 # DEBUG | INFO | WARNING | ERROR
  log_format: json                # json | console
  timezone: UTC                   # IANA tz name; cron expressions resolve here
  state_db: sqlite:///state.db    # SQLAlchemy URL or 'memory://'
  scrub_secrets: true             # never disable in production
  plugins:
    enabled: []                   # allowlist; non-empty wins over disabled
    disabled: []                  # denylist; ignored when enabled is set
  metrics:
    enabled: false                # GET /metrics on host:port when true
    host: 0.0.0.0
    port: 9090
```

`state_db` accepts either a SQLAlchemy URL (`sqlite:///PATH`,
`postgresql+asyncpg://...`) or the literal `memory://` for ephemeral
in-process state. `memory://` is intended for embedded mode and tests,
not the standalone daemon — the SQLite default is sufficient for
self-host.

`runtime.scrub_secrets` controls the structlog secret-scrubber
processor. Disabling it in production emits a startup warning and
allows credentials to appear in logs verbatim. Don't.

## `checkers`

A list of checker instances. Each entry binds a free-form `id` to a
plugin `type`. Built-in types: `rdap`, `whois`, `script`. Extra types
arrive via [plugins](../../architecture/0004-plugin-protocol.md).

```yaml
checkers:
  - id: rdap                     # used by domains[].checker
    type: rdap
    settings:
      timeout: 10s

  - id: whois
    type: whois
    settings:
      timeout: 30s

  - id: my-script
    type: script
    settings:
      command: ["/opt/checkers/check.sh"]
      timeout: 30s
```

The `id` is what `domains[].checker` references. The `type` selects
the plugin class. Same `type` may appear with different `id`s when an
operator wants distinct timeouts or scripts.

## `notifiers`

Same shape; plugin-driven. Built-in types: `telegram`, `email`,
`discord`, `webhook`.

```yaml
notifiers:
  - id: tg-ops
    type: telegram
    settings:
      bot_token: ${TG_BOT_TOKEN}
      chat_id: ${TG_OPS_CHAT}
      parse_mode: HTML

  - id: ops-email
    type: email
    settings:
      smtp_host: smtp.example.com
      smtp_port: 587
      smtp_username: ${SMTP_USER}
      smtp_password: ${SMTP_PASSWORD}
      from_addr: alerts@example.com
      to_addrs: ["ops@example.com"]
      use_starttls: true

  - id: ops-discord
    type: discord
    settings:
      webhook_url: ${DISCORD_WEBHOOK_URL}

  - id: ops-webhook
    type: webhook
    settings:
      url: https://hooks.example.com/dwatcher
      method: POST
      headers:
        X-Api-Key: ${HOOK_SECRET}
      body_template: '{"d":"${domain}","exp":"${expires_at}","sev":"${severity}","cid":"${cycle_id}"}'
```

Webhook templates accept exactly five placeholders: `${domain}`,
`${expires_at}`, `${threshold}`, `${severity}`, `${cycle_id}`. Any
other `${var}` is a startup error.

The `id` is what `domains[].channels[]` references — the dispatcher
treats every notifier as a channel for routing purposes.

## `notification_defaults`

```yaml
notification_defaults:
  thresholds: ["30d", "14d", "7d", "1d"]   # strictly descending, non-empty
  retry:
    max_attempts: 3
    base_delay: 1s
    factor: 5.0
```

Per-domain overrides live under `domains[].thresholds`.

## `parsing`

Static and runtime-learned WHOIS rules.

```yaml
parsing:
  whois_rules:
    - tld: com
      expires_regex: 'Registry Expiry Date:\s+(\S+)'
      date_format: iso8601
    - tld: ru
      expires_regex: 'paid-till:\s+(\S+)'
      date_format: iso8601
      timezone: UTC
    - tld: jp
      expires_regex: '\[Expires on\]\s+(.+)'
      date_format: custom
      strptime_format: '%Y/%m/%d'

  llm_fallback:
    enabled: false                 # opt-in
    suggester:
      type: litellm
      settings:
        model: ollama/gemma3
        api_base: http://ollama:11434
    safety:
      max_age_years: 50
      revalidate_after: 30d
      max_learn_per_hour: 5
      max_learn_per_tld_per_24h: 3
```

`expires_regex` MUST contain exactly one capture group. `date_format:
custom` requires `strptime_format`. See
[learned-rules.md](./learned-rules.md) for the LLM-fallback
threat-model summary and the on-by-default safety pipeline.

## `domains`

```yaml
domains:
  - name: example.com
    checker: rdap                # MUST exist in checkers[].id
    schedule: "0 */6 * * *"      # 5-field cron; APScheduler validates fully
    channels: [tg-ops, ops-email]  # MUST exist in notifiers[].id
    thresholds: ["60d", "14d"]   # optional; falls back to defaults
    metadata:
      owner: platform-team
      ticket: OPS-1234
```

`metadata` is opaque to the daemon and ferries arbitrary string
key/value pairs into the `Alert` envelope; useful for downstream
dashboards.

## Validation behaviour

- Cross-references (`checker`, `channels`) are validated against the
  registries at composition time. Unknown ids fail startup.
- `whois_rules[*].tld` MUST be unique.
- `notifiers[*].id` and `checkers[*].id` MUST be unique.
- Duration strings accept `s`/`m`/`h`/`d` suffixes (`30d`, `1h`, `15m`,
  `45s`); negative values are rejected.

## Hot reload

The watcher tails the YAML path. On rewrite (200 ms debounce) it loads
+ validates a new `Config`. Validation failures log ERROR and keep the
old config; success fans out a diff to subscribers (scheduler,
registries, parser). Notifier ids removed from the file finish their
in-flight `send()` calls with the OLD instance and refuse new dispatch.
