# Domain Watcher — Documentation

All documentation in this folder is in English.

## Contents

- [`architecture/`](./architecture/) — system design, ADRs, bounded-context
  references. Start with [`0001-overview.md`](./architecture/0001-overview.md).
- `guides/` *(forthcoming)* — operator setup, integrator how-tos.
- `reference/` *(forthcoming)* — auto-generated API reference.

## Audience

- **Operators** running the standalone Docker app — see `guides/operator/`.
- **Integrators** embedding the library (e.g. the Telegram bot repo) —
  see `guides/integrator/` and `architecture/0005-bot-integration.md`.
- **Contributors** modifying the core — start with the architecture
  overview, then the bounded-contexts ADR.
