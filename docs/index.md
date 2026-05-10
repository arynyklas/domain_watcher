# domain-watcher

Periodic domain expiration checker. RDAP / WHOIS / custom-script
strategies, multi-channel alerts (Telegram, Email, Discord, generic
webhook), runtime LLM-assisted WHOIS rule learning under safety rails.

`domain-watcher` ships in two shapes from the same source tree:

- a **standalone daemon** (Docker image, YAML config, hot reload), and
- a **Python library** (`domain_watcher`, async, builder API, no YAML
  required) for embedding inside another async host.

```{toctree}
:caption: Operator
:maxdepth: 2

guides/operator/quickstart
guides/operator/configuration
guides/operator/learned-rules
guides/operator/docker
```

```{toctree}
:caption: Integrator
:maxdepth: 2

guides/integrator/embedding
```

```{toctree}
:caption: Reference
:maxdepth: 2

reference/cli
reference/plugins
reference/api
```

```{toctree}
:caption: Project
:maxdepth: 1

changelog
```

## Indices

- {ref}`genindex`
- {ref}`modindex`
- {ref}`search`
