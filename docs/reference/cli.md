# CLI reference

Every subcommand also responds to `-h` / `--help` for the flag list.
This page documents intent and exit codes.

```text
domain-watcher run     --config PATH       # daemon, foreground
domain-watcher check   DOMAIN [--checker]  # one-shot, JSON output
domain-watcher version                     # __version__
domain-watcher config validate PATH        # 0 = valid, non-zero = error
domain-watcher rules learned [--tld T] [--purge-auto --yes]
domain-watcher rules show ID
domain-watcher rules promote ID            # prints YAML diff to stdout
domain-watcher rules disable ID
domain-watcher rules delete ID
domain-watcher rules revalidate [--all|ID|--below-pipeline-version N]
```

## `run --config PATH`

Boot the daemon in the foreground. SIGINT and SIGTERM trigger a clean
shutdown — the scheduler stops, in-flight `send()` calls drain, and
the process exits 0.

The `--config` flag overrides the default search path:

1. `--config PATH`
2. `DOMAIN_WATCHER_CONFIG` env var
3. `./domain-watcher.yaml`
4. `/etc/domain-watcher/config.yaml`
5. `$XDG_CONFIG_HOME/domain-watcher/config.yaml`

Hot reload is automatic: rewriting the resolved file triggers a
debounced re-parse and reconciliation.

## `check DOMAIN`

Run a single check and print the result as JSON. Useful for one-off
verification, dashboards, and shell pipelines:

```bash
domain-watcher check example.com --checker rdap | jq '.expires_at'
```

Exit codes:

| code | meaning                                                   |
| ---- | --------------------------------------------------------- |
| 0    | check succeeded; `expires_at` populated                   |
| 1    | transient or permanent failure (see `outcome` field)      |
| 2    | argument error (unknown checker, malformed domain)        |

## `config validate PATH`

Run the full Pydantic + cross-reference validators and print errors.

```bash
domain-watcher config validate ./domain-watcher.yaml
echo $?     # 0 = clean
```

## `rules` subcommands

Operate on the `learned_rules` state table written by the LLM-fallback
parser. See [`learned-rules.md`](../guides/operator/learned-rules.md)
for workflow guidance.

`rules learned --purge-auto` requires `--yes` (without it the command
exits 2 with a usage error). `rules promote` writes a YAML diff to
stdout and exits 0; the diff is intentionally not auto-applied.
