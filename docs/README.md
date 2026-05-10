# Domain Watcher — Documentation

All documentation in this folder is in English.

## Quick links

- [`guides/operator/quickstart.md`](./guides/operator/quickstart.md) —
  first alert in under five minutes.
- [`guides/operator/configuration.md`](./guides/operator/configuration.md) —
  every YAML key.
- [`guides/operator/learned-rules.md`](./guides/operator/learned-rules.md) —
  LLM-assisted WHOIS rule learning.
- [`guides/operator/docker.md`](./guides/operator/docker.md) — image,
  compose, secrets, LLM backend swap.
- [`guides/integrator/embedding.md`](./guides/integrator/embedding.md) —
  embed `domain_watcher` in another async app.
- [`reference/cli.md`](./reference/cli.md) — CLI subcommands.
- [`architecture/`](./architecture/) — ADRs and design notes; start with
  [`0001-overview.md`](./architecture/0001-overview.md).

## Audience

- **Operators** running the standalone Docker app — see
  [`guides/operator/`](./guides/operator/).
- **Integrators** embedding the library (the Telegram bot repo and any
  other async host) — see
  [`guides/integrator/embedding.md`](./guides/integrator/embedding.md)
  and [`architecture/0005-bot-integration.md`](./architecture/0005-bot-integration.md).
- **Plugin authors** — see
  [`architecture/0004-plugin-protocol.md`](./architecture/0004-plugin-protocol.md)
  and the conformance harnesses in `domain_watcher.testing`.
- **Contributors** modifying the core — start with the architecture
  overview, then the bounded-contexts ADR.
