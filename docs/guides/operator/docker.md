# Running domain-watcher with Docker

`docker/Dockerfile` produces a multi-stage build whose runtime stage is
[`gcr.io/distroless/python3-debian12:nonroot`][distroless] — no shell,
no apt, runs as `nonroot`. The image is ~80 MiB.

`docker/compose.yml` ships an `app + ollama` two-service stack. Ollama
is the default local backend for `parsing.llm_fallback.suggester` when
the LLM-assisted WHOIS rule learner is enabled; remove the service or
flip `enabled: false` in the config to disable it.

## Quick start

```bash
# 1. Build the image.
make docker-build         # = docker build -t domain-watcher:dev -f docker/Dockerfile .

# 2. Provide secrets via env or a .env file alongside compose.yml.
export TG_BOT_TOKEN=...   # Telegram Bot API token
export TG_OPS_CHAT=...    # chat id (string or "@handle")

# 3. Boot the stack.
make docker-up            # = docker compose -f docker/compose.yml up -d

# 4. Sanity-check.
docker exec domain-watcher-app-1 domain-watcher version
docker exec domain-watcher-app-1 domain-watcher config validate /etc/domain-watcher/config.yaml

# 5. Stop.
make docker-down
```

## Configuration mount

The example mounts `docker/example-config.yaml` read-only at
`/etc/domain-watcher/config.yaml`. To swap configs, point your own file
into the same path:

```yaml
services:
  app:
    volumes:
      - /etc/domain-watcher/config.yaml:/etc/domain-watcher/config.yaml:ro
```

The container reads `DOMAIN_WATCHER_CONFIG` first; it defaults to
`/etc/domain-watcher/config.yaml` so most operators never set it
explicitly.

## Secrets

`${VAR}` placeholders inside the YAML are resolved from the container's
environment at startup. Required vars that are not set abort startup
with a `ConfigError` — there is no silent fallback. Use a `.env` file
or your secret manager to ship them; do **not** bake secrets into the
image.

## State volume

The daemon persists its SQLite state DB to
`/var/lib/domain-watcher/state.db` (a named compose volume by default).
Mount your own volume if you want offline access to the file:

```yaml
services:
  app:
    volumes:
      - /srv/domain-watcher/state:/var/lib/domain-watcher
```

The directory is owned by `nonroot:nonroot`; `docker compose down -v`
discards it.

## Logs

The daemon writes structured JSON to stdout (`runtime.log_format: json`)
or human-readable lines (`console`). Compose's default driver tails
container stdout — no extra wiring required.

## Swapping the LLM backend

`parsing.llm_fallback.suggester.settings.model` is a single LiteLLM
provider/model string. Examples:

```yaml
parsing:
  llm_fallback:
    enabled: true
    suggester:
      type: litellm
      settings:
        model: ollama/gemma3              # local Ollama (default)
        api_base: http://ollama:11434
    # OR — cloud:
    # suggester:
    #   type: litellm
    #   settings:
    #     model: openai/gpt-4o-mini
    #     api_key: ${OPENAI_API_KEY}
```

When the model lives outside the compose network, drop the `ollama`
service and set `LLM_API_KEY` (or whatever your provider expects)
through the container environment.

[distroless]: https://github.com/GoogleContainerTools/distroless
