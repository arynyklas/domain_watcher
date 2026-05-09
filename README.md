# domain-watcher

Periodic domain expiration checker. RDAP / WHOIS / custom-script strategies,
multi-channel alerts (Telegram, Email, Discord, generic webhook), runtime
LLM-assisted WHOIS rule learning under safety rails.

Architecture: hexagonal (ports & adapters) with light DDD bounded contexts
in `core/`. See [docs/architecture/](docs/architecture/) for the full design.

## Quick start

```bash
make install        # install runtime + dev deps
make check          # lint + format-check + typecheck + imports-check + unit tests
```

License: MIT.
