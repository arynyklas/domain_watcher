# Changelog

All notable changes to `domain-watcher` are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [Unreleased]

### Changed

- Documentation moved to a Sphinx + MyST site published at
  [domain-watcher.readthedocs.io](https://domain-watcher.readthedocs.io/).
  The internal `docs/architecture/` ADRs and `docs/plans/` working notes
  were removed from the user-facing tree; the operator and integrator
  guides now stand alone.

### Added

- `.readthedocs.yaml` — Sphinx build pipeline (Python 3.12, uv-driven
  install of `.[docs]` extras, `fail_on_warning: true`).
- `docs/conf.py`, `docs/index.md`, `docs/reference/api.md`,
  `docs/reference/plugins.md`, `docs/changelog.md` — Sphinx scaffold,
  autosummary-driven public API reference, and CHANGELOG include.
- `docs` extra in `pyproject.toml` (sphinx, myst-parser, furo,
  sphinx-copybutton, sphinx-design, linkify-it-py, pytest — pytest is
  needed to import `domain_watcher.testing` for autodoc).
- GitHub Actions `docs` workflow building the Sphinx site on every PR
  and push, with `-W` warnings-as-errors and an advisory linkcheck pass.

## [0.1.0] — 2026-05-09

Initial public release. Library + standalone daemon. Bot integration
(see ADR 0005) ships from a separate repository.

### Added

#### Core
- Pure-domain bounded contexts (`monitoring`, `checking`, `parsing`,
  `notification`) with strictly typed entities, value objects, ports,
  and events. No third-party imports in `core/` (enforced via
  `import-linter`).
- `DomainName`, `Duration`, `ChannelId`, `CheckSchedule`, `LastCheck`,
  `MonitoredDomain`, `CheckResult`, `Alert`, `LearnedRule` value
  objects and aggregates.
- Two-tier event criticality (`critical` / `standard`) per ADR 0002 §6.

#### Application
- In-process event bus with both async-iterator and callback APIs and
  the two-tier delivery model from ADR 0001 §11(2).
- `CheckDomainUseCase`, `DispatchNotificationsUseCase`,
  `ParsingService` (LLM-fallback orchestrator), `RevalidationService`,
  `ConfigHolder`, `ChannelResolver`, `SchedulerService` port.

#### Infrastructure
- Checkers: `RdapChecker` (with IANA bootstrap cache), `WhoisChecker`
  (sync `python-whois` wrapped in `to_thread`), `ScriptChecker`
  (subprocess + JSON contract from ADR 0004 §7), and
  `WhoisCheckerWithParser` composite.
- Notifiers: Telegram (Bot API HTTP, no aiogram), SMTP, Discord
  webhook, generic HTTP webhook with `string.Template` body
  rendering.
- Parsers: `RegexWhoisParser`, six-gate `ValidationPipeline` from ADR
  0006 §4, `LiteLLMRuleSuggester`, in-process token bucket and
  per-TLD limiter, `SuggesterCircuitBreaker`.
- Persistence: memory repos plus SQLAlchemy 2 async repos for SQLite
  and Postgres, sharing one Alembic migration tree and one contract
  test suite.
- Scheduling: `ApsScheduler` with idempotent `add_or_update_job`,
  pre-tick reconcile from `repo.list_all()`, and a periodic
  revalidation job.
- Configuration: Pydantic v2 schema with cross-reference validation,
  YAML loader with `${ENV:-default}` interpolation, `watchdog`-backed
  `ConfigFileWatcher` with debounce + atomic-replace handling, and the
  reconciliation subscribers from ADR 0003 §5.
- Plugin discovery via `importlib.metadata.entry_points` for
  checkers / notifiers / parsers / rule_suggesters, with
  `runtime.plugins.enabled` / `disabled` filters and protocol-version
  refusal (ADR 0004 §5.2 / §9).
- Observability: structlog with JSON / console renderers and a
  case-insensitive secret-scrubbing processor; Prometheus counters,
  gauge, and histogram exposed via an opt-in aiohttp `/metrics`
  listener.

#### Interfaces
- Library façade `domain_watcher.DomainWatcher` with `from_config_file`
  and `builder` constructors, `start` / `stop`, `check_now`,
  `ensure_watching`, `remove_watching`, `events()`, `on(...)`,
  `on_any(...)`.
- Stable public re-exports under `domain_watcher` and
  `domain_watcher.adapters`.
- `domain_watcher.testing` — semver-protected test surface
  (`FixedClock`, memory repos, `PluginContractTest`,
  `CheckerContractTest`, `RepoContractTest`).
- Typer CLI: `run`, `check`, `version`, `config validate`, `rules`
  (`learned`, `show`, `promote`, `disable`, `delete`, `revalidate`).

#### Operations
- Multi-stage Dockerfile producing a distroless `nonroot` runtime
  image; compose stack with optional Ollama backend; named volume for
  the SQLite state DB.
- GitHub Actions CI runs lint, format-check, ty type-check,
  import-linter, unit tests in one job and integration tests (gated
  by a `docker info` preflight) in a second.
- GitHub Actions release workflow triggered on `v*` tags: builds wheel
  + sdist via `uv build`, publishes to PyPI through Trusted
  Publishing, pushes the container image to GHCR.

### Documentation
- Operator guides: quickstart, configuration reference, learned-rules
  workflow, Docker reference.
- Integrator guide: embedding `domain_watcher` in another async app.
- CLI reference.
- Six ADRs (0001–0006) documenting the architecture.

### Explicitly out of scope
- The Telegram bot itself (ships from a separate repository — see ADR
  0005). No `aiogram` dependency, no bot code in this tree.

[Unreleased]: https://github.com/arynyklas/domain-watcher/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/arynyklas/domain-watcher/releases/tag/v0.1.0
