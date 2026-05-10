# domain-watcher

Periodic domain expiration checker. RDAP / WHOIS / custom-script
strategies, multi-channel alerts (Telegram, Email, Discord, generic
webhook), runtime LLM-assisted WHOIS rule learning under safety rails.

Architecture: hexagonal (ports & adapters) with light DDD bounded
contexts in `core/`. Documentation is published at
[domain-watcher.readthedocs.io](https://domain-watcher.readthedocs.io/).

## Quick start (Docker)

```bash
cp docker/example-config.yaml ./domain-watcher.yaml
$EDITOR ./domain-watcher.yaml
export TG_BOT_TOKEN=... TG_OPS_CHAT=...

make docker-build
make docker-up
docker compose -f docker/compose.yml logs -f app
```

Full guide: [docs/guides/operator/quickstart.md](docs/guides/operator/quickstart.md).

## Quick start (development)

```bash
make install        # runtime + dev deps
make check          # ruff + ty + import-linter + unit tests
make test-all       # also runs integration + e2e
```

## Embed as a library

```python
from domain_watcher import DomainWatcher, DomainName, Duration
from domain_watcher.adapters import RdapChecker, MemoryMonitoredDomainRepo

watcher = (
    DomainWatcher.builder()
    .with_repo(MemoryMonitoredDomainRepo())
    .with_checker(RdapChecker(timeout=10.0))
    .with_notifier(my_notifier)
    .build()
)
await watcher.start()
await watcher.ensure_watching(
    DomainName("example.com"), checker_id="rdap", channels=["my-chan"]
)
```

See [docs/guides/integrator/embedding.md](docs/guides/integrator/embedding.md).

## Documentation

- [docs/guides/operator/](docs/guides/operator/) — running the daemon
- [docs/guides/integrator/](docs/guides/integrator/) — embedding the library
- [docs/reference/cli.md](docs/reference/cli.md) — CLI subcommands
- [docs/reference/plugins.md](docs/reference/plugins.md) — plugin protocol

License: MIT.
