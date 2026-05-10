# Quickstart — first alert in under five minutes

Goal: a Docker-deployed `domain-watcher` daemon paged you the moment a
test domain crosses a threshold. Everything below assumes Docker and a
Telegram bot. Swap channels later — Telegram is the fastest to verify.

## 1. Get a Telegram bot

Talk to [@BotFather](https://t.me/BotFather), run `/newbot`, copy the
token. Send a message to your new bot, then visit
`https://api.telegram.org/bot<TOKEN>/getUpdates` to find your numeric
`chat.id`.

## 2. Clone and configure

```bash
git clone https://github.com/arynyklas/domain-watcher.git
cd domain-watcher

cp docker/example-config.yaml ./domain-watcher.yaml
$EDITOR ./domain-watcher.yaml         # set domains[].name

export TG_BOT_TOKEN=...               # from BotFather
export TG_OPS_CHAT=123456789          # numeric chat id
```

Pick a domain that expires inside the smallest threshold (default
`1d`) so you'll get an alert on the first scheduled tick. Or override
`thresholds:` to `["365d"]` while testing — your future renewals
already cross that.

## 3. Run

```bash
make docker-build         # ~80 MiB distroless image
make docker-up            # docker compose up -d

docker compose -f docker/compose.yml logs -f app
```

Within `0 */6 * * *` (every six hours by default — change `schedule:`
to `*/2 * * * *` while testing) the daemon will check, dispatch, and
log a `notification.dispatched` event. Telegram pings.

## 4. Validate

```bash
docker exec domain-watcher-app-1 domain-watcher version
docker exec domain-watcher-app-1 \
    domain-watcher check example.com --checker rdap   # one-shot, JSON output
docker exec domain-watcher-app-1 \
    domain-watcher config validate /etc/domain-watcher/config.yaml
```

Common failures:

| Symptom                                    | Cause                                                          |
| ------------------------------------------ | -------------------------------------------------------------- |
| `ConfigError: env var TG_BOT_TOKEN unset`  | Forgot `export`; pass via `.env` or the compose `environment`. |
| `domain example.com: checker 'rdap' is not declared` | Mismatch between `domains[].checker` and `checkers[].id`.      |
| Telegram silent, no error                  | Most likely the bot has not been started by the recipient. Send any message to the bot first. |

## 5. Next steps

- [`configuration.md`](./configuration.md) — every YAML key explained.
- [`learned-rules.md`](./learned-rules.md) — when to enable LLM fallback.
- [`docker.md`](./docker.md) — image internals, volumes, secrets, swapping LLM backend.
