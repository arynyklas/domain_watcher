# Domain Watcher — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development
> (if subagents available) or superpowers:executing-plans to implement this plan.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `domain_watcher` core library and standalone application
described in [ADRs 0001–0006](../architecture/). The bot repository is **out
of scope** — see ADR 0005's hard boundary banner.

**Architecture:** Hexagonal / Ports & Adapters with light DDD bounded
contexts in `core/`. Async-first. YAML config is source of truth. Runtime
LLM fallback for WHOIS rules, gated by a 6-step validation pipeline.

**Tech stack:** Python 3.12+, `httpx`, `python-whois`, `apscheduler`,
`watchdog`, `pydantic` v2, `typer`, `structlog`, SQLAlchemy 2 async +
`asyncpg`/`aiosqlite`, `pytest`-asyncio, `litellm` for the rule suggester
`uv` for packaging. **Explicitly NOT** in this repo: `aiogram`, any bot code.

**Plan layout (chunks):**

- Chunk 1 — Phase 0–3: scaffold, core, application, persistence ← *this file*
- Chunk 2 — Phase 4–6: checkers, parsers, notifiers
- Chunk 3 — Phase 7–8: configuration, scheduling, hot reload
- Chunk 4 — Phase 9–10: interfaces (CLI + library), composition, Docker
- Chunk 5 — Phase 11: plugin protocol, contract test harness, release prep

Each chunk is self-reviewed before the next is implemented.

---

## Reading order

Before writing any code, the executor MUST read, in order:

1. `docs/architecture/0001-overview.md` — bird's-eye, layers, file layout.
2. `docs/architecture/0002-bounded-contexts.md` — exact entities, VOs, events, ports.
3. `docs/architecture/0006-runtime-llm-fallback.md` — read **once** before
   touching parsing.
4. `docs/architecture/0003-config-schema.md`, `0004-plugin-protocol.md` —
   reference as needed.

If any task contradicts an ADR, the ADR wins; flag the contradiction and
stop.

## Conventions for every task

- TDD: write the failing test first, run it, watch it fail, then implement.
- Commit after each task; one feat/fix/refactor commit per task.
- Lint clean: `make check` (runs ruff, ty, import-linter, unit tests).
- `core/` MUST NOT import third-party libs except `typing_extensions`. CI
  enforces this with `import-linter` (added in Phase 0 Task 4).
- All datetimes are tz-aware UTC. `datetime.utcnow()` is forbidden; use the
  injected `TimeProvider`.
- All public names ship with type annotations. No `Any` in core.

---

## Chunk 1 — Phase 0–3

# Phase 0 — Foundation & tooling

Goal: a buildable, lintable, testable empty repo. No business logic yet.

## Task 0.1 — uv project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `README.md`
- Create: `LICENSE` (MIT)
- Create: `.gitignore`
- Create: `src/domain_watcher/__init__.py` (just `__version__ = "0.0.0"`)
- Create: `tests/__init__.py`

**Steps:**

- [ ] **1. Initialize uv project**

```bash
uv init --package --name domain-watcher --python 3.12
```

- [ ] **2. Pin dependencies in pyproject.toml**

Production deps (Phase 0 ships only what's needed to build):

```
dependencies = []
```

Dev deps (`[project.optional-dependencies] dev`):

```
"pytest>=8",
"pytest-asyncio>=0.23",
"pytest-cov>=5",
"ruff>=0.5",
"ty>=0.0.1a8",
"import-linter>=2.0",
```

Tool sections:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E","F","I","B","UP","RUF","SIM","TC","TID","ASYNC"]

[tool.ty]
# ty is the Astral type checker (mypy successor). Configure rules
# explicitly; "strict" semantics emulated via per-rule severities.
src.root = "src"
environment.python-version = "3.12"
rules.possibly-unresolved-reference = "error"
rules.unused-ignore-comment = "warn"

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "-q --strict-markers"
```

- [ ] **3. Write LICENSE (MIT)** with current year and project name.

- [ ] **4. Write `.gitignore`** with `.venv/`, `__pycache__/`, `.pytest_cache/`,
  `.mypy_cache/`, `.ty_cache/`, `.ruff_cache/`, `dist/`, `*.egg-info/`, `.coverage*`,
  `htmlcov/`, `state.db*`.

- [ ] **5. Verify install**
  ```bash
  make install
  uv run python -c "import domain_watcher; print(domain_watcher.__version__)"
  ```


Expected: `0.0.0`.

- [ ] **6. Commit**: `chore: scaffold uv project`

## Task 0.2 — directory skeleton

**Files (all empty `__init__.py` unless noted):**

```
src/domain_watcher/
├── core/
│   ├── shared/
│   ├── monitoring/
│   ├── checking/
│   ├── parsing/
│   └── notification/
├── application/
│   ├── use_cases/
│   └── services/
├── infrastructure/
│   ├── checkers/
│   ├── parsers/
│   ├── notifiers/
│   ├── persistence/
│   ├── scheduling/
│   └── config/
├── interfaces/
│   ├── cli/
│   └── library/
└── testing/                    # public test helpers (Phase 11)
tests/
├── unit/
│   ├── core/
│   ├── application/
│   └── infrastructure/
├── integration/
└── e2e/
```

**Steps:**

- [ ] **1. Create the tree** (use `find` after to verify; commit empties).
- [ ] **2. Run `pytest`** — expected: `0 collected, 0 errors`.
- [ ] **3. Commit**: `chore: directory skeleton matching ADR 0001`.

## Task 0.3 — Makefile

Single source of truth for every developer-facing command. CI calls
`make` targets; nothing in the docs ever spells out a long `uv run …`
chain again.

**Files:**
- Create: `Makefile`
- Create: `tests/test_makefile_targets.py`

**Steps:**

- [ ] **1. Write `Makefile`** with these targets (exact contents):

```makefile
# domain-watcher — developer commands
# Convention: every target is .PHONY; tab-indented recipes; `make help` lists them.
SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help install sync test test-unit test-integration test-e2e \
        lint format format-check typecheck imports-check \
        check ci clean run docker-build docker-up docker-down

help:  ## list targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## install runtime + dev deps into the project venv
	$(UV) sync --extra dev

sync: install  ## alias for install

lint:  ## ruff lint
	$(UV) run ruff check

format:  ## ruff auto-format
	$(UV) run ruff format

format-check:  ## ruff format check (no writes)
	$(UV) run ruff format --check

typecheck:  ## ty type-check (mypy successor)
	$(UV) run ty check

imports-check:  ## verify layered architecture rules
	$(UV) run lint-imports

test: test-unit  ## default = unit tests only

test-unit:  ## fast unit tests (no I/O)
	$(UV) run pytest -q tests/unit

test-integration:  ## integration tests (Docker required)
	$(UV) run pytest -q -m integration

test-e2e:  ## end-to-end tests
	$(UV) run pytest -q tests/e2e

test-all: test-unit test-integration test-e2e  ## every test suite

check: lint format-check typecheck imports-check test-unit  ## local pre-commit gate

ci: check  ## what CI runs (integration added by the workflow when Docker is up)

clean:  ## remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .ty_cache .mypy_cache dist build htmlcov .coverage*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +

run:  ## run the daemon against ./domain-watcher.yaml
	$(UV) run domain-watcher run --config ./domain-watcher.yaml

docker-build:  ## build the docker image
	docker build -t domain-watcher:dev -f docker/Dockerfile .

docker-up:  ## start docker compose stack
	docker compose -f docker/compose.yml up -d

docker-down:  ## stop docker compose stack
	docker compose -f docker/compose.yml down
```

- [ ] **2. Write `tests/test_makefile_targets.py`** — guards against a
  regression where the plan and the Makefile drift apart:

  The test compares the *set* of targets `make help` lists to this exact set literal:

  ```python
  EXPECTED_TARGETS = {
      "help", "install", "sync",
      "lint", "format", "format-check", "typecheck", "imports-check",
      "test", "test-unit", "test-integration", "test-e2e", "test-all",
      "check", "ci",
      "clean", "run",
      "docker-build", "docker-up", "docker-down",
  }
  ```

  The assertion is `set(parsed_targets) == EXPECTED_TARGETS` — extra
  targets fail the test, missing targets fail the test. Update the set
  together with the Makefile when adding new targets.

- [ ] **3. Run `make help`** — expected: prints exactly the 20 targets in
  EXPECTED_TARGETS.
- [ ] **4. Run `make check`** — expected: passes (still no business code).
- [ ] **5. Commit**: `chore(make): single-source command runner`.

## Task 0.4 — import-linter contract

**Files:**
- Create: `.importlinter`
- Modify: `Makefile` (add `imports-check` target — done in Task 0.3)

**Steps:**

- [ ] **1. Write `.importlinter`** enforcing layer rules from ADR 0001 §4:

```ini
[importlinter]
root_packages = domain_watcher

[importlinter:contract:layers]
name = layered architecture
type = layers
layers =
    domain_watcher.interfaces
    domain_watcher.application
    domain_watcher.infrastructure
    domain_watcher.core

[importlinter:contract:core_purity]
name = core has no third-party imports
type = forbidden
source_modules = domain_watcher.core
forbidden_modules =
    httpx
    pydantic
    sqlalchemy
    apscheduler
    watchdog
    typer
    structlog
ignore_imports =
    domain_watcher.core.* -> typing_extensions
```

- [ ] **2. Run `make imports-check`** — expected: passes (everything is empty).
- [ ] **3. Commit**: `chore(lint): import-linter contracts for layered arch`.

## Task 0.5 — CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Steps:**

- [ ] **1. Workflow runs `make ci`** which itself runs:
  `make lint`, `make format-check`, `make typecheck`, `make imports-check`,
  `make test`, then `make test-integration` (in a separate job that runs
  `docker info >/dev/null` as a preflight — fails fast if Docker is
  unreachable; the integration job MUST NOT skip silently).
- [ ] **2. Push & verify** the workflow turns green on the empty repo.
- [ ] **3. Commit**: `ci: make-driven gate (ruff + ty + import-linter + pytest)`.

## Task 0.6 — no-Any-in-core lint (custom AST check)

**Files:**
- Create: `tests/lint/test_no_any_in_core.py`
- Modify: `Makefile` (add `make check` already runs `test-unit`, no change needed)

**Steps:**

- [ ] **1. AST walker** that opens every `.py` under `src/domain_watcher/core/` and fails the test if any annotation node contains `Any`. Implementation: parse with `ast.parse`, walk, look for `ast.Name(id="Any")` and `ast.Attribute(attr="Any")` inside `ast.AnnAssign.annotation`, `ast.arg.annotation`, and function returns. Allow-list nothing — `Any` in core is a code smell, period.
- [ ] **2. Failing test:** introduce `core/checking/_scratch.py` with `def f(x: Any) -> None: ...`, assert the test fails. Remove the file.
- [ ] **3. Run `make test`** — passes against the (still empty) core tree.
- [ ] **4. Commit**: `chore(lint): no-Any-in-core AST check`.

---

# Phase 1 — Core domain layer (pure, no I/O)

Goal: every entity, value object, event, and port from ADR 0002 lives in
code with passing unit tests. Zero third-party deps in `core/`.

> Reference: every dataclass and Protocol below is specified in
> [ADR 0002](../architecture/0002-bounded-contexts.md). Field lists,
> invariants, and docstrings come from that ADR; this plan only covers
> *order of work, files, and tests*.

## Task 1.1 — `core/shared/value_objects.py`

**Files:**
- Create: `src/domain_watcher/core/shared/value_objects.py`
- Create: `tests/unit/core/shared/test_value_objects.py`

**Steps:**

- [ ] **1. Write failing tests** for `DomainName`:
  - normalizes case, strips trailing dot
  - rejects empty / overlong (>253) / bad-label inputs
  - IDN domains converted to punycode
  - `tld` returns last label
  - `registrable` returns eTLD+1 (use `tldextract` *only inside* a helper
    placed in `infrastructure/`; do **not** import here — `core/` is pure.
    For `tld` and `registrable`, hardcode minimal logic in v1: split on `.`
    and treat last label as tld, last 2 as registrable, with the
    well-known double TLDs hand-listed: `co.uk`, `com.br`, `co.jp`,
    `org.uk`, `gov.uk`. Add a TODO referencing PSL improvement in Phase 11.)

- [ ] **2. Tests for `Duration`:** `Duration.days(7).seconds == 604800`;
  `Duration.parse("30d") == Duration.days(30)`; supports `s`, `m`, `h`, `d`;
  rejects negative.

- [ ] **3. Implement** both VOs as `@dataclass(frozen=True, slots=True)` with
  `__post_init__` validation.

- [ ] **4. Tests pass; `make typecheck` clean.**

- [ ] **5. Commit**: `feat(core/shared): DomainName and Duration value objects`.

## Task 1.2 — `core/shared/errors.py`

**Files:**
- Create: `src/domain_watcher/core/shared/errors.py`
- Create: `tests/unit/core/shared/test_errors.py`

**Steps:**

- [ ] **1. Write failing test** asserting hierarchy from ADR 0002 §1
  (`DomainWatcherError` ⇒ `ConfigError`, `CheckingError` ⇒ `Transient/Permanent`,
  `ParseError` ⇒ `NoMatchingRule/Suggestion/RuleValidation`, `NotificationError`
  ⇒ `DeliveryFailed`).
- [ ] **2. Implement.**
- [ ] **3. Commit**: `feat(core/shared): error hierarchy`.

## Task 1.3 — `core/shared/time_provider.py`

**Files:**
- Create: `src/domain_watcher/core/shared/time_provider.py`
- Create: `tests/unit/core/shared/test_time_provider.py`

**Steps:**

- [ ] **1. Define `TimeProvider` Protocol** with `now() -> datetime` (UTC).
- [ ] **2. Define `FixedClock(TimeProvider)`** for tests (advances on demand).
  Live in `core/shared/` — it's a pure helper, not I/O.
- [ ] **3. Tests:** `FixedClock(at).now() == at`; `advance()` works; rejects
  naive datetimes.
- [ ] **4. Commit**: `feat(core/shared): TimeProvider port + FixedClock`.

## Task 1.4 — `core/monitoring/`

**Files:**
- Create: `src/domain_watcher/core/monitoring/value_objects.py` —
  `ChannelId`, `CheckSchedule`, `LastCheck`.
- Create: `src/domain_watcher/core/monitoring/entities.py` — `MonitoredDomain`.
- Create: `src/domain_watcher/core/monitoring/events.py` — `DomainAdded`,
  `DomainRemoved`, `DomainCheckRequested`, plus `DomainEvent` base.
- Create: `src/domain_watcher/core/monitoring/ports.py` —
  `MonitoredDomainRepository`.
- Create: `tests/unit/core/monitoring/test_*`.

**Steps:**

- [ ] **1. `DomainEvent` base** in `events.py` — fields `occurred_at`,
  `correlation_id`, plus `criticality: ClassVar[Literal["critical", "standard"]] = "standard"`
  per ADR 0002 §6. The two-tier bus (Task 2.1) reads this attribute.
- [ ] **2. VOs** with invariants from ADR 0002 §2.
- [ ] **3. `MonitoredDomain`** with invariants:
  - `notify_thresholds` non-empty, strictly descending
  - `channels` non-empty
  - `with_check_result(r)` returns a new instance with monotonic `last_check.at`
  - `is_due(now)` reflects schedule + last_check
- [ ] **4. Repository Protocol** — port only, no impl. Test that a
  `Mapping[DomainName, MonitoredDomain]`-backed in-test fake satisfies it
  via `isinstance(fake, MonitoredDomainRepository)` + `runtime_checkable`.
- [ ] **5. Commit**: `feat(core/monitoring): aggregate, VOs, events, port`.

## Task 1.5 — `core/checking/`

**Files:**
- Create: `src/domain_watcher/core/checking/value_objects.py` — `CheckOutcome`,
  `CheckResult`.
- Create: `src/domain_watcher/core/checking/policies.py` — `RetryPolicy`.
- Create: `src/domain_watcher/core/checking/events.py` —
  `DomainCheckCompleted`, `DomainCheckFailed`.
- Create: `src/domain_watcher/core/checking/ports.py` — `ExpirationChecker`.
- Create: `tests/unit/core/checking/test_*`.

**Steps:**

- [ ] **1. `CheckResult.__post_init__`** enforces: `outcome == OK ⇔ expires_at is not None`,
  `outcome != OK ⇔ error is not None`. Test both directions, with
  `pytest.raises(ValueError)`.
- [ ] **2. `RetryPolicy.delay_for(attempt)`** returns geometric backoff;
  test bounds + non-negative.
- [ ] **3. Events + Protocol.** Mark `DomainCheckFailed.criticality = "critical"`
  per ADR 0002.
- [ ] **4. Commit**: `feat(core/checking): result VOs, retry policy, port`.

## Task 1.6 — `core/parsing/`

**Files:**
- Create: `src/domain_watcher/core/parsing/value_objects.py` — `DateFormat`,
  `RegexPattern`, `ParseRule`, `LearnedRule`.
- Create: `src/domain_watcher/core/parsing/events.py` — `ParseFailed`,
  `WhoisRuleLearned`, `WhoisRuleRevalidated`, `WhoisRuleInvalidated`.
- Create: `src/domain_watcher/core/parsing/ports.py` — `WhoisParser`,
  `RuleSuggester`, `LearnedRulesRepository`.
- Create: `tests/unit/core/parsing/test_*`.

**Steps:**

- [ ] **1. `RegexPattern.__post_init__`** compiles eagerly; bad regex raises
  `ValueError`.
- [ ] **2. `ParseRule.__post_init__`:** exactly one capture group;
  `CUSTOM ⇒ strptime_format set`. Property tests using stdlib `re` only.
- [ ] **3. `LearnedRule`** dataclass with the metadata listed in ADR 0006 §9
  (id, tld, regex, format, tz, auto_learned, disabled, suggester_id,
  pipeline_version, sample hash, created_at, …).
- [ ] **4. Events + ports** per ADR 0002. Mark `ParseFailed.criticality = "critical"`
  and `WhoisRuleInvalidated.criticality = "critical"`.
- [ ] **5. Commit**: `feat(core/parsing): VOs, events, parser+suggester+repo ports`.

## Task 1.7 — `core/notification/`

**Files:**
- Create: `src/domain_watcher/core/notification/entities.py` — `Channel`,
  `Alert`, `AlertSeverity`.
- Create: `src/domain_watcher/core/notification/policies.py` —
  `NotificationPolicy`.
- Create: `src/domain_watcher/core/notification/events.py` —
  `NotificationDispatched`, `NotificationFailed`. Mark
  `NotificationFailed.criticality = "critical"`.
- Create: `src/domain_watcher/core/notification/ports.py` — `Notifier`,
  `IdempotencyStore`, `ChannelResolver`.
- Create: `tests/unit/core/notification/test_*`.

**Steps:**

- [ ] **1. `NotificationPolicy.alerts_for(prev, current, now)`** is **pure**:
  no DB, no clock — `now` is passed in. Tests:
  - `prev=None`, `current=OK with expires_at = now+30d`, `thresholds=[30d,7d,1d]`
    → exactly one Alert at the 30d threshold, with `cycle_id = sha256(expires_at.isoformat())[:16]`.
  - `prev=LastCheck(at=earlier, expires_at=X, outcome=OK)`,
    `current=OK with expires_at=X` (same expiration; no renewal), same
    thresholds → no new alerts (no new crossing).
  - `prev` had `expires_at=X` and 30d-threshold already crossed;
    `current=OK with expires_at=X+1y` (renewal) → 30d crossing emitted
    again with a NEW `cycle_id`. Idempotency keyed by
    `(domain, threshold, cycle_id, channel)` so the new cycle is not
    suppressed by older records.
  - `current` non-OK → no alerts.
  - thresholds tested in descending order against `expires_at - now`.
  No claim about "already fired" — that check belongs to the
  IdempotencyStore (see Task 2.6).
- [ ] **2. Severity assignment**: 30d→INFO, 7d→WARNING, 1d→CRITICAL by default.
- [ ] **3. `IdempotencyStore` Protocol** with the 4-tuple key from ADR 0002 §5
  (`domain`, `threshold`, `cycle_id`, `channel`). Tests: same key → already_fired
  returns True after record(); different cycle_id → already_fired returns False
  (renewal correctly re-fires); different channel → False (per-recipient).
  Memory fake in `tests/unit/core/notification/_fakes.py`.
- [ ] **4. Commit**: `feat(core/notification): policy, alert entity, ports`.

## Task 1.8 — purity gate

- [ ] **1. Run `make imports-check`** — must pass with **0** violations.
- [ ] **2. Run full test suite** — all green, <1s, no I/O.
- [ ] **3. Commit if any tweaks**: `chore(core): enforce purity in CI`.

---

# Phase 2 — Application layer

Goal: orchestrators that consume `core/` ports. Tests use in-memory fakes
only — no real I/O. The hot LLM-fallback flow lives here, not in plugins.

## Task 2.1 — `application/event_bus.py`

**Files:**
- Create: `src/domain_watcher/application/event_bus.py`
- Create: `tests/unit/application/test_event_bus.py`

The bus exposes both APIs locked in by ADR 0001 §11(2).

**Steps:**

- [ ] **1. `EventPublisher` Protocol** in `core/shared/events.py`:
  `async def publish(event: DomainEvent) -> None`.
- [ ] **2. `InProcessEventBus`** in `application/event_bus.py`:
  - `publish(event)` fans out to (a) registered callbacks and (b) every
    subscribed iterator's queue.
  - `events()` returns an `AsyncIterator[DomainEvent]`; cancellation
    cleanly drops the queue.
  - `on(EventType, async_handler)` registers a typed callback.
  - `on_any(async_handler)` registers a wildcard.
  - **Two-tier delivery model.** Each subscriber maintains two queues:
    - **Critical queue** (unbounded). Used for events whose class declares
      `criticality: ClassVar[Literal["critical", "standard"]] = "critical"`.
      v1 critical events: `NotificationFailed`, `WhoisRuleInvalidated`,
      `ParseFailed`, `DomainCheckFailed`. Publishers `await queue.put` with
      a per-subscriber timeout (default 5s); on timeout, log ERROR with
      event id + subscriber id, skip *that subscriber* for *that event*,
      and continue with other subscribers. Critical events are NEVER
      silently dropped.
    - **Standard queue** (bounded, default `maxsize=1024`). Drop-oldest
      with a `BusOverflow` event published into the same standard queue.
      `BusOverflow` is itself standard so a wedged subscriber cannot
      cause an unbounded loop.
- [ ] **3. Tests** for: ordering, cancellation, type filtering, callback
  exception isolation (one handler raising MUST NOT block others), cleanup
  on iterator GC, plus:
  - critical event: slow subscriber blocks for 5s → ERROR log emitted, other
    subscribers receive the event normally, publisher continues
  - standard event: queue full → drop oldest, BusOverflow appears in queue
  - critical event class is detected via the `criticality` ClassVar
- [ ] **4. Commit**: `feat(app): in-process event bus with iterator + callbacks`.

## Task 2.2 — `application/unit_of_work.py`

**Files:**
- Create: `src/domain_watcher/application/unit_of_work.py`
- Create: `tests/unit/application/test_uow.py`

**Steps:**

- [ ] **1. Define `UnitOfWork` Protocol** with `__aenter__`, `__aexit__`,
  and a `commit()` / `rollback()` shape that adapters fulfil.
- [ ] **2. `MemoryUnitOfWork`** — no-op for tests.
- [ ] **3. Commit**: `feat(app): unit-of-work port + memory fake`.

## Task 2.3 — `application/use_cases/check_domain.py`

**Files:**
- Create: `src/domain_watcher/application/use_cases/check_domain.py`
- Create: `tests/unit/application/test_check_domain.py`

**Behavior:**

```
input: DomainName
1. Fetch MonitoredDomain from repo (or 404)
2. Resolve checker by id (Registry — see Phase 4)
3. Apply RetryPolicy with TransientCheckError
4. On success: domain.with_check_result → repo.update
5. Publish DomainCheckCompleted (or DomainCheckFailed)
```

**Steps:**

- [ ] **1. Tests with fakes**: success path; transient error retried;
  permanent error no-retry; missing domain raises; missing checker raises.
- [ ] **2. Implement.**
- [ ] **3. Commit**: `feat(app): check_domain use case`.

## Task 2.4 — `application/services/parsing_service.py`

**Files:**
- Create: `src/domain_watcher/application/services/parsing_service.py`
- Create: `tests/unit/application/test_parsing_service.py`

This is the heart of ADR 0006. Re-read it before starting.

**Behavior:** see ADR 0006 §3 flowchart.

**Dependencies (injected):**
- `WhoisParser`
- `LearnedRulesRepository`
- `RuleSuggester | None` (None ⇒ fallback disabled)
- `ValidationPipeline` (Phase 5; for now a Protocol stub here)
- `EventPublisher`
- `TimeProvider`
- `RateLimiter` (per-host + per-TLD; tiny in-process token bucket)

**Steps:**

- [ ] **1. Define `ValidationPipeline` Protocol** in `core/parsing/ports.py`
  (we add it now so application can depend on it; impl in Phase 5).
- [ ] **2. Define `RateLimiter` Protocol** in `core/shared/ports.py`.
- [ ] **3. Tests:**
  - all-static-rules path: returns datetime, no LLM call
  - learned-rules path: static fails, learned matches, returns datetime
  - LLM fallback disabled: emits ParseFailed, no suggester call
  - LLM fallback success: suggester returns rule → validation passes →
    repo.add called with sha256 of raw text → returns datetime →
    `WhoisRuleLearned` event published
  - LLM fallback validation failure: emits ParseFailed; rule NOT persisted
  - rate limit exceeded: emits ParseFailed; suggester NOT called
  - SuggestionError treated as transient ParseFailed
- [ ] **4. Implement** with explicit step ordering matching ADR 0006 §3.
- [ ] **5. Commit**: `feat(app): parsing service with LLM fallback orchestration`.

## Task 2.5 — `application/services/revalidation_service.py`

**Files:**
- Create: `src/domain_watcher/application/services/revalidation_service.py`
- Create: `tests/unit/application/test_revalidation.py`

Periodic health check on learned rules (ADR 0006 §5).

**Steps:**

- [ ] **1. Tests:**
  - Picks rules whose `last_revalidated_at + revalidate_after < now`.
  - On validation pass: `mark_revalidated`, `WhoisRuleRevalidated` event.
  - On failure: `disable`, `WhoisRuleInvalidated` event with reason.
- [ ] **2. Implement.** Service exposes `run_once()`; the scheduler will
  drive it in Phase 8.
- [ ] **3. Commit**: `feat(app): periodic learned-rules revalidation`.

## Task 2.6 — `application/use_cases/dispatch_notifications.py`

**Files:**
- Create: `src/domain_watcher/application/use_cases/dispatch_notifications.py`
- Create: `tests/unit/application/test_dispatch_notifications.py`

**Behavior:**

```
on DomainCheckCompleted(result):
  policy.alerts_for(prev, result, now) → alerts
  for each alert:
    for each Channel returned by resolver.channels_for(domain):
      if idempotency.already_fired(alert.domain, alert.threshold, alert.cycle_id, channel.id): skip
      try notifier.send(alert, channel) under RetryPolicy
      on success: idempotency.record(...); publish NotificationDispatched
      on exhaustion: publish NotificationFailed
```

**Steps:**

- [ ] **1. Tests** for each branch with fakes (FakeNotifier with
  configurable failure modes, FakeIdempotencyStore, FakePolicy):
  - one channel raises permanently, two others succeed → both successes
    publish NotificationDispatched; the failure publishes NotificationFailed;
    no in-flight delivery is cancelled.
- [ ] **2. Implement.** Use `asyncio.gather(*per_channel_coros, return_exceptions=True)` for
  fan-out — NOT structured concurrency that cancels healthy siblings on
  the first exception, dropping deliveries to unaffected channels.
  Each per-channel coroutine catches `DeliveryFailedError` itself and
  publishes `NotificationFailed`; the gather call surfaces any unexpected
  exceptions for logging without aborting the rest.
- [ ] **3. Commit**: `feat(app): notification dispatch with retry+idempotency`.

## Task 2.6a — `ChannelResolver` and StaticChannelResolver

**Files:**
- Create: `src/domain_watcher/application/channel_resolver.py` —
  `StaticChannelResolver` (default impl).
- Modify: dispatch use case from Task 2.6 to consume `ChannelResolver`
  rather than reading `domain.channels` directly.
- Create: `tests/unit/application/test_channel_resolver.py`.

**Steps:**

- [ ] **1. `ChannelResolver` Protocol** is defined in `core/notification/ports.py`
  per ADR 0002 §5 (already required there). Static impl iterates `domain.channels`,
  looks up each `ChannelId` in `NotifierRegistry`, and returns
  `Channel(id, notifier_id, routing={})` instances.
- [ ] **2. Tests:** unknown channel id → raises with the missing id named;
  empty `domain.channels` returns empty sequence (legal in embedded mode
  when a non-static resolver is wired).
- [ ] **3. Wire DispatchNotificationsUseCase** to call `resolver.channels_for(domain)`
  before iterating; idempotency check is per (domain, threshold, cycle_id, channel_id).
- [ ] **4. Commit**: `feat(app): ChannelResolver port + static impl`.

## Task 2.7 — `application/use_cases/reload_config.py` (skeleton)

**Files:**
- Create: `src/domain_watcher/application/use_cases/reload_config.py`
- Create: `tests/unit/application/test_reload_config.py`

Phase 7 fills in Pydantic schema + watcher; this task creates the
`ConfigHolder` shape and subscriber protocol.

**Steps:**

- [ ] **1. Define `ConfigSubscriber` Protocol** (`on_config_changed(old, new)`).
- [ ] **2. `ConfigHolder` impl** (atomic swap, subscriber fan-out, exception
  isolation per ADR 0003 §5).
- [ ] **3. Tests** with a stub `Config = SimpleNamespace`-style dataclass
  for now; real Pydantic schema arrives in Phase 7.
- [ ] **4. Commit**: `feat(app): config holder with subscriber fan-out`.

## Task 2.8 — `application/scheduling.py` (port)

**Files:**
- Create: `src/domain_watcher/application/scheduling.py`
- Create: `tests/unit/application/test_scheduling_port.py`

**Steps:**

- [ ] **1. Define `SchedulerService` Protocol** with:
  `add_job(domain_name, cron, callable)`, `remove_job(domain_name)`,
  `list_jobs()`, `start()`, `stop()`, `reconcile(domains)`.
- [ ] **2. `reconcile()` algorithm test** (using a memory fake):
  - existing job, identical schedule → leave alone
  - existing job, changed schedule → remove + add
  - new domain → add
  - removed domain → remove
- [ ] **3. Commit**: `feat(app): scheduler port + reconcile contract`.

---

# Phase 3 — Persistence adapters

Goal: every `core/` repository port has a memory and SQL impl. Postgres
ships in this phase even though the bot repo is the heaviest user (ADR
0001 §10 tradeoff: centralize generic persistence here).

## Task 3.1 — memory adapters

**Files:**
- Create: `src/domain_watcher/infrastructure/persistence/memory/`:
  - `monitored.py` — `MemoryMonitoredDomainRepo`
  - `learned_rules.py` — `MemoryLearnedRulesRepo`
  - `idempotency.py` — `MemoryIdempotencyStore`
  - `__init__.py` re-exports
- Create: `tests/unit/infrastructure/persistence/memory/test_*.py`

**Steps:**

- [ ] **1. Implement each repo** as `dict`-backed with an asyncio lock.
- [ ] **2. Property-style tests:** add then get; remove non-existent;
  `due_for_check` returns only due domains; idempotency ↔ already_fired
  monotonicity; `MemoryIdempotencyStore` keyed by 4-tuple per D1; renewal
  yields new cycle_id and re-fires; per-channel keys are distinct;
  `LearnedRule` UNIQUE on (tld, regex).
- [ ] **3. Commit**: `feat(infra/memory): repos for monitored/learned/idempotency`.

## Task 3.2 — SQLAlchemy 2 async setup

**Files:**
- Create: `src/domain_watcher/infrastructure/persistence/sql/__init__.py`
- Create: `src/domain_watcher/infrastructure/persistence/sql/orm.py`
- Create: `src/domain_watcher/infrastructure/persistence/sql/uow.py`
- Create: `src/domain_watcher/infrastructure/persistence/sql/migrations/`
- Modify: `pyproject.toml` (add `sqlalchemy[asyncio]>=2.0`, `aiosqlite`,
  `asyncpg`, `alembic`).

**Steps:**

- [ ] **1. Declare ORM tables** mapping ADR 0006 §9 + monitoring +
  `alert_idempotency` with primary key (`domain_name`, `threshold_secs`,
  `cycle_id`, `channel_id`). `MappedAsDataclass` style for explicit
  columns. Use a `MetaData` with naming conventions for stable migration names.
- [ ] **2. `SqlUnitOfWork`** wraps `async_sessionmaker` and yields
  per-context `AsyncSession`.
- [ ] **3. Alembic init** + first revision auto-generated.
- [ ] **4. Run alembic against an in-memory SQLite** in tests.
- [ ] **5. Commit**: `feat(infra/sql): ORM, UoW, alembic init`.

## Task 3.3 — SQL repositories

**Files:**
- Create: `src/domain_watcher/infrastructure/persistence/sql/repos/monitored.py`
- Create: `src/domain_watcher/infrastructure/persistence/sql/repos/learned_rules.py`
- Create: `src/domain_watcher/infrastructure/persistence/sql/repos/idempotency.py`
- Create: `tests/integration/persistence/sql/test_*.py`

**Steps:**

- [ ] **1. Tests run against a `aiosqlite:///:memory:` engine** with
  alembic-applied schema; identical assertion suite as memory tests
  (parameterize the test class so we run the same suite for both backends).
- [ ] **2. Tests run against Postgres** in `integration` only, using
  `testcontainers-python` to spin up `postgres:16-alpine`. Skip if
  Docker not available; mark with `pytest.mark.integration`.
- [ ] **3. Implement** each repo with `select`/`insert`/`upsert` (use
  `sqlite_upsert`/`pg_upsert` — abstract via SQLAlchemy 2's `on_conflict`).
- [ ] **4. Commit**: `feat(infra/sql): MonitoredDomain/LearnedRules/Idempotency repos`.

## Task 3.4 — shared repository contract test

**Files:**
- Create: `tests/contracts/test_repository_contract.py`

**Steps:**

- [ ] **1. Define a shared test class** parameterized over (memory, sqlite,
  postgres) repo factories. Asserts a long list of behaviors that any
  conforming repo MUST satisfy — this is the executable definition of
  the port. Future plugin authors of new repos run this against their
  impl.
- [ ] **2. Commit**: `test: shared repository contract suite`.

## Task 3.5 — purity & layer gate (re-check)

- [ ] **1. Run `make imports-check`** — passes; sql code is in `infrastructure/`.
- [ ] **2. Run `make typecheck`** clean.
- [ ] **3. End-of-chunk verification:** `make ci`.
- [ ] **4. Commit if any tweaks**: `chore: end-of-chunk verification`.

---

## End of Chunk 1

At this point the repository has:

- A typed, tested **pure core** (no I/O).
- An **application layer** that orchestrates parsing-with-LLM-fallback,
  domain checks, notification dispatch, and config holding — all behind
  port abstractions, all tested with in-memory fakes.
- A **persistence layer** with memory + SQLite + Postgres impls, sharing a
  single contract suite.

What is **not** done yet (intentionally):

- Real network I/O (RDAP, WHOIS, SMTP, HTTP webhooks) — **Chunk 2**.
- Pydantic config schema, hot-reload watcher — **Chunk 3**.
- APScheduler integration — **Chunk 3**.
- CLI, library façade, Docker — **Chunk 4**.
- Plugin entry-points, contract test harness, release prep — **Chunk 5**.

Do **not** proceed to Chunk 2 until Chunk 1 passes review.

---

## Chunk 2 — Phase 4–6

# Phase 4 — Checkers (network I/O)

Goal: three concrete `ExpirationChecker` adapters plus retry-aware tests.
All checkers honor the `id: ClassVar[str]` contract (ADR 0004 §4.1).

## Task 4.1 — checker registry

**Files:**
- Create: `src/domain_watcher/infrastructure/registry.py`
- Create: `tests/unit/infrastructure/test_registry.py`

**Steps:**

- [ ] **1. Implement `Registry[T]`** — a typed name→instance store with
  `register(obj)`, `get(id)`, `all()`, raises `PluginConflictError` on
  duplicate id.
- [ ] **2. Tests:** register/get; conflict; lookup-miss raises.
- [ ] **3. Commit**: `feat(infra): typed plugin registry`.

## Task 4.2 — RDAP checker

**Files:**
- Create: `src/domain_watcher/infrastructure/checkers/rdap.py`
- Create: `src/domain_watcher/infrastructure/checkers/_iana_bootstrap.py`
- Create: `tests/unit/infrastructure/checkers/test_rdap.py`
- Create: `tests/integration/checkers/test_rdap_real.py`
- Create: `tests/fixtures/rdap/` — captured RDAP JSON for ≥4 TLDs (com,
  ru, app, dev), plus a 404 sample.

**Steps:**

- [ ] **1. `IanaBootstrap`** caches `https://data.iana.org/rdap/dns.json`
  for 24h; resolves `tld → rdap_base_url`. Lives behind a port so tests
  can inject a fake.
- [ ] **2. Failing test:** RDAP 200 with `events[].eventAction == "expiration"`
  → `CheckResult.OK` with parsed `expires_at`.
- [ ] **3. Failing tests** for each branch: 404 → PERMANENT; 5xx → TRANSIENT;
  connection reset → TRANSIENT; malformed JSON → PERMANENT.
- [ ] **4. Implement `RdapChecker`** with `httpx.AsyncClient` (timeout from
  settings); `id = "rdap"`.
- [ ] **5. Integration test** marked `pytest.mark.integration` hits real
  IANA bootstrap and `iana.org` RDAP server; skipped offline.
- [ ] **6. Commit**: `feat(infra/checkers): RDAP checker with IANA bootstrap`.

## Task 4.3 — WHOIS checker

**Files:**
- Create: `src/domain_watcher/infrastructure/checkers/whois.py`
- Create: `tests/unit/infrastructure/checkers/test_whois.py`
- Modify: `pyproject.toml` (`python-whois>=0.9`)

**Steps:**

- [ ] **1. Failing tests** with monkeypatched `whois.whois`:
  - returns dict-like with parseable text → `CheckResult.OK(raw=text)`
    where `expires_at` is **None** (the parser, not the checker, derives
    the date — checker hands raw text downstream).
  - timeout → TRANSIENT
  - "no match" line in raw → PERMANENT (regex check on common strings;
    explicit list in the test).
- [ ] **2. Implement.** Wrap `whois.whois` in `asyncio.to_thread` because
  `python-whois` is sync.
- [ ] **3. Note in module docstring**: this checker delegates parsing to
  `WhoisParser`; it never sets `expires_at`. The orchestrator wires the
  parsing service after this checker.
- [ ] **4. Commit**: `feat(infra/checkers): WHOIS checker (python-whois, sync→async)`.

## Task 4.4 — script checker

**Files:**
- Create: `src/domain_watcher/infrastructure/checkers/script.py`
- Create: `tests/unit/infrastructure/checkers/test_script.py`
- Create: `tests/fixtures/scripts/` — `ok.sh`, `transient.sh`, `bad-json.sh`.

**Steps:**

- [ ] **1. Failing tests** for each branch (ADR 0004 §7 contract):
  - exit 0 + valid JSON OK → CheckResult.OK
  - exit 0 + valid JSON transient_error → TRANSIENT
  - exit 1 with no JSON → PERMANENT, stderr captured (truncated to 4 KiB)
  - non-JSON stdout → PERMANENT
  - timeout → TRANSIENT, process killed (verify via `pgrep` in fixture
    aftermath OR by asserting `proc.returncode != 0`)
- [ ] **2. Implement** with `asyncio.create_subprocess_exec`,
  `wait_for(timeout)`, `proc.kill()` on timeout. argv: `[*command, domain_name]`.
- [ ] **3. Commit**: `feat(infra/checkers): script checker with JSON contract`.

## Task 4.5 — orchestrator wiring (parsing for WHOIS)

**Files:**
- Create: `src/domain_watcher/infrastructure/checkers/_whois_with_parser.py`
- Create: `tests/unit/infrastructure/checkers/test_whois_with_parser.py`

**Steps:**

- [ ] **1. `WhoisCheckerWithParser`** is the public registry entry under id
  `"whois"`. Composition wires this composite, never the bare fetcher.
  Rename `WhoisChecker` (Task 4.3) to `_WhoisFetcher` — leading underscore,
  same module, NOT registered, NOT in `domain_watcher.adapters`.
  `WhoisCheckerWithParser` injects `_WhoisFetcher` at construction and
  hands the raw text off to `ParsingService`.
- [ ] **2. Test** that `Registry.all()` contains `"whois"` exactly once
  after composition and that `_WhoisFetcher` is not exported via the
  public adapters surface.
- [ ] **3. Tests:** raw text parses → OK with date; ParseError → PERMANENT.
- [ ] **4. Commit**: `feat(infra/checkers): WHOIS+parser composite checker`.

---

# Phase 5 — Parsers

## Task 5.1 — `RegexWhoisParser`

**Files:**
- Create: `src/domain_watcher/infrastructure/parsers/regex.py`
- Create: `tests/unit/infrastructure/parsers/test_regex.py`
- Create: `tests/fixtures/whois/` — captured WHOIS for `.com`, `.ru`,
  `.co.uk`, `.app`, `.io`.
- Create: `tests/fixtures/whois/CAPTURE.md` — for each fixture, records
  the `whois <fqdn>` command and ISO capture date. Required so
  contributors can regenerate when registries change format.

**Steps:**

- [ ] **1. Failing tests** for each fixture: rules from ADR 0003 §3
  example yield expected `datetime` (timezone-aware UTC).
- [ ] **2. Test no-match**: `NoMatchingRuleError` raised with TLD in message.
- [ ] **3. Test bad-date**: matched but parse fails → `ParseError`.
- [ ] **4. Implement** — apply rules in `for rule in rules: if rule.tld matches`,
  return first successful parse. UTC-normalize using `zoneinfo.ZoneInfo(rule.timezone)`.
- [ ] **5. Commit**: `feat(infra/parsers): regex-driven WHOIS parser`.

## Task 5.2 — `ValidationPipeline`

**Files:**
- Create: `src/domain_watcher/infrastructure/parsers/validation_pipeline.py`
- Create: `src/domain_watcher/infrastructure/parsers/data/known_good_domains.json`
- Create: `tests/unit/infrastructure/parsers/test_validation_pipeline.py`

The 6-gate pipeline from ADR 0006 §4. Each gate is its own method;
tests target each gate in isolation.

**Embedded data shape:**

```json
{
  "version": 1,
  "tlds": {
    "com": ["iana.org", "verisign.com", "icann.org"],
    "ru":  ["nic.ru", "ripn.net"],
    "uk":  ["nic.uk"],
    "io":  ["nic.io"]
  }
}
```

At least 20 TLDs at v1. Add a CI check that each entry is a real,
currently-resolvable domain (run weekly, not per-PR).

**Steps:**

- [ ] **1. Failing tests** per gate from ADR 0006 §4:
  - gate 1 (compile + 1 group)
  - gate 2 (matches the same WHOIS)
  - gate 3 (parses to datetime)
  - gate 4 (range check: not past, not >50y future)
  - gate 5 (cross-check on known-good; mock `WhoisChecker.check`)
  - gate 6 (rejects 1970-01-01 etc.)
  - gate 5 cache: same TLD known-good fetched twice within revalidate_after
    → second call hits cache, no network call (assert mock not called twice)
  - gate 5 transient: known-good WHOIS raises TransientCheckError → gate
    raises SuggestionError(transient=True), NOT RuleValidationError; rule
    is NOT rejected, the learn attempt is retryable next time
- [ ] **2. Test pipeline_version constant** — bump documentation when
  rules tighten.
- [ ] **3. Implement.** Gate 5 calls an injected `WhoisChecker`; if no
  known-good domain for the TLD, gate 5 is **skipped with a warning event**
  (per ADR 0006).
  - Gate 5 caches `(tld, known_good_domain) → raw_whois` for
    `safety.revalidate_after`. Use a tiny TTL dict (no extra dep);
    revalidation drives expiry. A process restart costs at most one extra
    fetch per learn attempt.
  - Cross-check WHOIS fetch raising `TransientCheckError` →
    `SuggestionError(transient=True)`, NOT `RuleValidationError`.
- [ ] **4. Test** that skipped/transient gate-5 increments
  `domain_watcher_pipeline_gate5_skipped_total{reason}` with the right
  reason label (`no_known_good`, `cross_check_unavailable`).
- [ ] **5. Commit**: `feat(infra/parsers): 6-gate validation pipeline + known-good data`.

## Task 5.3 — `LiteLLMRuleSuggester`

**Files:**
- Create: `src/domain_watcher/infrastructure/parsers/llm_suggester.py`
- Create: `tests/unit/infrastructure/parsers/test_llm_suggester.py`
- Modify: `pyproject.toml` (add `litellm>=1.40`).

**Steps:**

- [ ] **1. Failing tests** mocking `litellm.acompletion`:
  - happy path: model returns JSON object with `expires_regex`, `date_format`,
    `timezone` → returns valid `ParseRule`
  - LLM returns malformed JSON → `SuggestionError`
  - LLM returns invalid regex (no group) → `SuggestionError`
  - `litellm.exceptions.Timeout` → `SuggestionError`
  - `litellm.exceptions.APIConnectionError` / 5xx → `SuggestionError`
  - `litellm.exceptions.AuthenticationError` → `SuggestionError` with
    `permanent=True` flag (caller treats as non-retryable)
  - `temperature=0` and `response_format={"type":"json_object"}` are
    forwarded to the call
  - settings round-trip: `model`, `api_base`, `api_key` reach `acompletion`
- [ ] **2. Prompt template** in `_prompts.py`:
  - System: tight role description; outputs JSON only.
  - User: includes raw WHOIS (truncated to 4 KiB), TLD, FQDN.
  - Example: one-shot example for `.com` to anchor format.
  Pin the exact template; changes go through a separate task with re-runs
  of the validation harness.
- [ ] **3. Implement** as a thin `LiteLLMRuleSuggester` calling
  `await litellm.acompletion(model=..., messages=..., api_base=...,
  api_key=..., temperature=0, timeout=...,
  response_format={"type":"json_object"})`. `id = "litellm"`.
- [ ] **4. Commit**: `feat(infra/parsers): LiteLLM-backed rule suggester`.

## Task 5.4 — Rate limit (in ParsingService) and circuit breaker (infra)

**Files:**
- Create: `src/domain_watcher/infrastructure/parsers/safety.py`
- Create: `tests/unit/infrastructure/parsers/test_safety.py`

**Steps:**

- [ ] **1. `TokenBucketLimiter`** — per-host max-per-hour from ADR 0006 §7.
  Tests advance an injected clock; verify acquire returns False when
  exhausted, True after refill.
- [ ] **2. `PerTldLimiter`** — max 3/24h per TLD.
- [ ] **3. `CircuitBreaker`** — open after 5 consecutive failures within 5m,
  half-open after 5m, closed after 1 success.
- [ ] **4. `SuggesterCircuitBreaker`** wraps the `RuleSuggester`. NO rate
  limiting here — that lives in `ParsingService` (Task 2.4). The breaker
  short-circuits open-circuit calls with `SuggestionError("circuit_open",
  transient=True)` and does not invoke the backend.
- [ ] **5. `ParsingService` integration test** — verify per-host
  `max_learn_per_hour` and per-TLD `max_learn_per_tld_per_24h` are enforced
  in the application layer, not the wrapper.
- [ ] **6. Commit**: `feat(infra/parsers): SuggesterCircuitBreaker; rate-limit lives in ParsingService`.

## Task 5.5 — wire ParsingService against recorded/live LLM impls

**Files:**
- Create: `tests/integration/parsing/test_parsing_with_recorded_llm.py`
- Create: `tests/integration/parsing/test_parsing_with_real_llm.py`
  (skip unless `LLM_INTEGRATION=1`; reads `LLM_MODEL`, `LLM_API_BASE`,
  `LLM_API_KEY` from env)
- Create: `tests/fixtures/llm/` — captured `litellm.acompletion` responses
  per scenario (`unknown_tld_ok.json`, `bad_json_response.json`,
  `missing_capture_group.json`, `auth_failure.json`).

**Steps:**

- [ ] **1. Recorded-fixture test (default CI)** mocks `litellm.acompletion`
  to return the contents of `tests/fixtures/llm/unknown_tld_ok.json` and
  asserts a `WhoisRuleLearned` event fires + a `LearnedRule` row appears.
  Default invocation in CI runs only this — it is fully hermetic.
- [ ] **2. Live-LLM test** is gated behind `LLM_INTEGRATION=1` and runs the
  same end-to-end scenario against a real backend (default
  `LLM_MODEL=ollama/gemma3` against an Ollama service container). Skipped
  otherwise. Marked `@pytest.mark.flaky(reruns=2)` because small local
  models occasionally produce malformed JSON even at temperature=0; the
  pipeline rejects malformed output, so a rerun exercises the same code
  path.
- [ ] **3. Capture script** at `tests/fixtures/llm/CAPTURE.md` documents
  how to regenerate fixtures (model id + prompt SHA + capture date).
- [ ] **4. Commit**: `test(integration): recorded-fixture LLM run as default; live LLM gated`.

---

# Phase 6 — Notifiers

## Task 6.1 — Telegram notifier (single-channel, httpx)

**Files:**
- Create: `src/domain_watcher/infrastructure/notifiers/telegram.py`
- Create: `tests/unit/infrastructure/notifiers/test_telegram.py`

**Settings (Pydantic model):** `bot_token`, `chat_id`, `parse_mode = "HTML"`.

**Steps:**

- [ ] **1. Failing tests** with mocked httpx:
  - happy path POSTs to `https://api.telegram.org/bot<token>/sendMessage`
  - 429 → `DeliveryFailedError` (retryable)
  - 401 → `NotificationError` (permanent — invalid token)
  - 5xx → `DeliveryFailedError`
  - body is HTML-escaped from the `Alert`'s `domain` and `expires_at`
- [ ] **2. Implement.** **Do NOT use aiogram** — direct Bot API HTTP only
  (ADR 0001 §8 row "Telegram (core)"; ADR 0005 banner). `id = "telegram"`.
- [ ] **3. Commit**: `feat(infra/notifiers): single-channel Telegram via Bot API HTTP`.

## Task 6.2 — Email SMTP notifier

**Files:**
- Create: `src/domain_watcher/infrastructure/notifiers/email_smtp.py`
- Create: `tests/unit/infrastructure/notifiers/test_email.py`
- Modify: `pyproject.toml` (`aiosmtplib>=3`)

**Steps:**

- [ ] **1. Failing tests** with `aiosmtplib`'s test mode:
  - STARTTLS path
  - plain SMTP rejected unless `allow_insecure=true`
  - auth failure → permanent `NotificationError`
  - server unavailable → `DeliveryFailedError`
- [ ] **2. Implement** with multipart `text/plain` + `text/html`.
- [ ] **3. Integration test** with a `mailpit` container
  (`testcontainers-python`).
- [ ] **4. Commit**: `feat(infra/notifiers): SMTP notifier`.

## Task 6.3 — Discord webhook notifier

**Files:**
- Create: `src/domain_watcher/infrastructure/notifiers/discord.py`
- Create: `tests/unit/infrastructure/notifiers/test_discord.py`

**Steps:**

- [ ] **1. Failing tests** with mocked httpx — POST to `webhook_url` with
  `{content, embeds: [{title, description, color}]}`. 429 → retryable.
- [ ] **2. Implement.**
- [ ] **3. Commit**: `feat(infra/notifiers): Discord webhook`.

## Task 6.4 — Generic webhook notifier

**Files:**
- Create: `src/domain_watcher/infrastructure/notifiers/webhook.py`
- Create: `tests/unit/infrastructure/notifiers/test_webhook.py`

**Settings:** `url`, `method = "POST"`, `headers: dict[str,str]`,
`body_template: str` (Python `string.Template` — `$var` / `${var}`).
Supported placeholders: `${domain}`, `${expires_at}`, `${threshold}`,
`${severity}`, `${cycle_id}`. Unknown placeholders are a startup error
(eager template validation in NotifierConfig).

**Steps:**

- [ ] **1. Failing tests:** template rendering; status mapping (2xx ok,
  4xx permanent, 5xx retryable); custom headers honored; secrets in
  headers never logged.
- [ ] **2. Implement.** Use stdlib `string.Template` directly. ADR 0003 §3
  has been updated to use `${var}` syntax — verify the example matches.
- [ ] **3. Commit**: `feat(infra/notifiers): generic HTTP webhook`.

## Task 6.5 — notifier contract test

**Files:**
- Create: `tests/contracts/test_notifier_contract.py`

**Steps:**

- [ ] **1. Parameterized class** running every notifier through the same
  conformance suite from ADR 0004 §4.2:
  - "send raises DeliveryFailedError when transport is down"
  - "send is idempotent under retry (sender does not assume prior delivery)"
  - "constructor validates settings eagerly"
  - "secrets do not appear in repr/logs"
- [ ] **2. Commit**: `test: shared notifier contract suite`.

---

## End of Chunk 2

Now the system can: fetch RDAP/WHOIS, parse WHOIS deterministically,
learn rules at runtime under safety rails, and deliver alerts over four
channels — all behind ports, all individually tested.

Still missing: configuration loading, hot reload, scheduling. Chunk 3.

---

## Chunk 3 — Phase 7–8

# Phase 7 — Configuration & hot reload

## Task 7.1 — Pydantic config schema

**Files:**
- Create: `src/domain_watcher/infrastructure/config/schema.py`
- Create: `src/domain_watcher/infrastructure/config/_duration_field.py`
- Create: `tests/unit/infrastructure/config/test_schema.py`
- Create: `tests/fixtures/config/` — `valid.yaml`, `missing-checker.yaml`,
  `bad-cron.yaml`, `dup-id.yaml`, `unresolved-env.yaml`.

**Steps:**

- [ ] **1. Implement models** per ADR 0003 §4. Use `Annotated[Duration, ...]`
  with a custom validator parsing "30d" / "1h" strings into the core `Duration`.
- [ ] **2. Cross-reference validators** (ADR 0003 §6):
  - `domains[*].checker ∈ checkers[*].id`
  - `domains[*].channels[*] ∈ notifiers[*].id`
  - `whois_rules[*].tld` unique
  - `notifiers[*].id` unique
  - `domains[*].thresholds` strictly descending non-empty
- [ ] **3. `model_config = ConfigDict(frozen=True, extra="forbid")`.**
- [ ] **4. Failing tests** for each invalid fixture — explicit error messages
  (asserting on the message, not just the exception type).
- [ ] **5. Commit**: `feat(infra/config): Pydantic schema with cross-ref validation`.

## Task 7.2 — Loader (YAML + env interpolation)

**Files:**
- Create: `src/domain_watcher/infrastructure/config/loader.py`
- Create: `tests/unit/infrastructure/config/test_loader.py`

**Steps:**

- [ ] **1. Failing tests:**
  - resolves `${NAME}` → env value
  - `${NAME:-default}` falls back when unset
  - missing required env var raises `ConfigError("env var X unresolved")`
  - precedence per ADR 0003 §2: CLI flag > `DOMAIN_WATCHER_CONFIG` env
    > `./domain-watcher.yaml` > `/etc/...` > XDG
- [ ] **2. Implement.** Use stdlib `os.path.expandvars` after a
  pre-pass that disambiguates `:-` defaults.
- [ ] **3. Commit**: `feat(infra/config): YAML loader with env interpolation`.

## Task 7.3 — `ConfigFileWatcher`

**Files:**
- Create: `src/domain_watcher/infrastructure/config/watcher.py`
- Create: `tests/unit/infrastructure/config/test_watcher.py`
- Modify: `pyproject.toml` (`watchdog>=4`)

**Steps:**

- [ ] **1. Failing tests** with `tmp_path`:
  - file rewrite → `ConfigHolder.update` called with new Config
  - debounce: two writes within 200ms → one update
  - validation failure → old config kept, ERROR log, no exception
    propagates
  - file move + replace (editor save pattern) handled
- [ ] **2. Implement.** Wrap `watchdog.observers.Observer` in an asyncio
  bridge using `asyncio.run_coroutine_threadsafe`.
- [ ] **3. Commit**: `feat(infra/config): hot-reload watcher with debounce`.

## Task 7.4 — Reconciliation subscribers

**Files:**
- Modify: `src/domain_watcher/application/use_cases/reload_config.py`
- Create: `tests/unit/application/test_reconcile.py`

**Subscribers** (ADR 0003 §5):
- `SchedulerSubscriber` — diffs domain set, calls scheduler reconcile.
- `RegistrySubscriber` — re-instantiates checker/notifier whose settings changed.
- `ParsingSubscriber` — replaces whois_rules table (atomic swap).

**Steps:**

- [ ] **1. Tests** assert: unchanged domains keep their schedule; settings
  changes trigger re-instantiation only for affected ids; secret-only changes
  bypass re-instantiation if a notifier exposes a `reload(settings)` hook.
  - notifier id removed from YAML → after the next reload, in-flight
    `Notifier.send()` calls finish with the OLD instance (drained), and
    new dispatch attempts to that id raise `PluginNotFoundError`. Test
    uses a fake notifier that blocks on a barrier mid-`send`; reload
    config; release barrier; assert old send completes; assert new
    dispatch raises.
- [ ] **2. Implement.** Each subscriber owns its diff logic.
- [ ] **3. Commit**: `feat(app): reconciliation subscribers for hot reload`.

---

# Phase 8 — Scheduling

## Task 8.1 — APScheduler adapter

**Files:**
- Create: `src/domain_watcher/infrastructure/scheduling/apscheduler.py`
- Create: `tests/unit/infrastructure/scheduling/test_apscheduler.py`
- Modify: `pyproject.toml` (`apscheduler>=3.10`)

**Steps:**

- [ ] **1. Failing tests:**
  - `add_job(domain, cron, callable)` registers an `AsyncIOScheduler` job
    with stable id `f"check:{domain.value}"`.
  - `reconcile(domains)` matches Task 2.8's contract test exactly.
  - `start()` / `stop()` are idempotent.
  - misfire policy: drop missed runs (we'll re-check at the next slot).
  - `add_or_update_job(domain, cron, callable_)` — used by
    `DomainWatcher.ensure_watching`. Idempotent: same args twice does not
    duplicate the job; cron change re-schedules the existing job in place.
  - `start()` reconciles from `repo.list_all()` BEFORE any external trigger
    (YAML reload or `ensure_watching`) so embedded callers see scheduled
    jobs immediately.
- [ ] **2. Implement.**
- [ ] **3. Commit**: `feat(infra/scheduling): APScheduler-backed SchedulerService`.

## Task 8.2 — Revalidation job wiring

**Files:**
- Modify: `src/domain_watcher/infrastructure/scheduling/apscheduler.py`
  — add `add_revalidation_job(interval)`.
- Create: `tests/unit/infrastructure/scheduling/test_revalidation_job.py`

**Steps:**

- [ ] **1. Test** that `add_revalidation_job(Duration.days(1))` schedules
  `RevalidationService.run_once` at the requested interval.
- [ ] **2. Implement.**
- [ ] **3. Commit**: `feat(infra/scheduling): periodic revalidation job`.

---

## End of Chunk 3

Configuration is loadable, validated, and hot-reloadable; scheduling
drives both per-domain checks and periodic revalidation. The service can
now run as a daemon — but it has no entry point yet.

---

## Chunk 4 — Phase 9–10

# Phase 9 — Interfaces

## Task 9.1 — Library API façade (`DomainWatcher`)

**Files:**
- Create: `src/domain_watcher/interfaces/library/api.py`
- Create: `src/domain_watcher/interfaces/library/builder.py`
- Create: `tests/unit/interfaces/test_library_api.py`

**Public surface** (re-exported from `domain_watcher` top-level):

```python
class DomainWatcher:
    @classmethod
    def from_config_file(cls, path: str | Path) -> "DomainWatcher": ...
    @classmethod
    def builder(cls) -> "DomainWatcherBuilder": ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def check_now(self, domain: DomainName) -> CheckResult: ...

    async def ensure_watching(
        self,
        domain: DomainName,
        *,
        checker_id: str,
        schedule: str = "0 */6 * * *",
        channels: Sequence[ChannelId],
        thresholds: Sequence[Duration] | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Idempotent: upsert MonitoredDomain → scheduler.add_or_update_job."""

    async def remove_watching(self, domain: DomainName) -> None:
        """scheduler.remove_job → repo.remove. Idempotency rows preserved."""

    def events(self) -> AsyncIterator[DomainEvent]: ...
    def on(self, event_type: type[E], handler: Callable[[E], Awaitable[None]]) -> None: ...
    def on_any(self, handler: Callable[[DomainEvent], Awaitable[None]]) -> None: ...
```

**Steps:**

- [ ] **1. Builder tests:**
  - missing required adapter (no checker or no notifier) → builder raises
    on `.build()`
  - registers everything passed in
  - configures defaults from `with_default_thresholds(...)`
- [ ] **2. Façade tests** (with everything stubbed):
  - `start` boots scheduler + event bus
  - `stop` is idempotent and cancels jobs cleanly
  - `events()` yields events as they fire
  - `on(EventType, h)` calls handler when matching event published
  - `ensure_watching(d, ...)` then `ensure_watching(d, ...)` is a no-op on
    the second call (no duplicate scheduler job, no extra repo write
    beyond an upsert)
  - `remove_watching(d)` cancels the scheduler job and removes the repo
    row but leaves alert_idempotency rows for that domain intact
  - `start()` reconciles the scheduler from `repo.list_all()` even when no
    YAML file is wired (embedded-mode path)
- [ ] **3. Implement.**
- [ ] **4. Commit**: `feat(interfaces): public DomainWatcher façade + builder`.

## Task 9.2 — top-level re-exports

**Files:**
- Modify: `src/domain_watcher/__init__.py`
- Create: `src/domain_watcher/adapters/__init__.py` (re-exports concrete adapters)
- Create: `tests/unit/test_public_api.py`

**Steps:**

- [ ] **1. Test** that the following imports succeed and types are stable:
  ```python
  from domain_watcher import (
      DomainWatcher, DomainName, Duration,
      CheckResult, CheckOutcome,
      Alert, AlertSeverity,
      DomainCheckCompleted, DomainCheckFailed,
      WhoisRuleLearned,
      ParseFailed, NotificationFailed,
  )
  from domain_watcher.adapters import (
      RdapChecker, WhoisChecker, ScriptChecker,
      TelegramNotifier, EmailNotifier, DiscordNotifier, WebhookNotifier,
      RegexWhoisParser, LiteLLMRuleSuggester,
      MemoryMonitoredDomainRepo, SqlMonitoredDomainRepo,
  )
  ```
- [ ] **2. Test** that nothing under `domain_watcher.core` is importable
  from `domain_watcher.adapters` (use `pkgutil` walk + `import-linter`
  reverse contract).
- [ ] **3. Commit**: `feat: stable public API re-exports`.

## Task 9.3 — CLI (typer)

**Files:**
- Create: `src/domain_watcher/interfaces/cli/app.py`
- Create: `src/domain_watcher/interfaces/cli/_rules.py`
- Create: `src/domain_watcher/interfaces/cli/_check.py`
- Create: `tests/unit/interfaces/cli/test_app.py`
- Modify: `pyproject.toml` — add CLI script entry:
  ```toml
  [project.scripts]
  domain-watcher = "domain_watcher.interfaces.cli.app:cli"
  ```

**Commands** (each is a separate failing test → impl → commit cycle):

- [ ] **1. `domain-watcher run --config PATH`** — boots the daemon
  (foreground). Test: launches, processes a SIGINT, exits cleanly.
- [ ] **2. `domain-watcher check DOMAIN [--checker rdap]`** — one-shot
  check; prints JSON. Tests: success and failure output schemas.
- [ ] **3. `domain-watcher rules learned [--tld T]`** — list learned rules.
- [ ] **4. `domain-watcher rules show ID`** — full rule + sample WHOIS hash.
- [ ] **5. `domain-watcher rules promote ID`** — emits a YAML diff for the
  operator to paste into config; **does not write the YAML directly**
  (operator chooses where to add it). Test asserts diff format.
- [ ] **6. `domain-watcher rules disable ID` / `delete ID`.**
- [ ] **7. `domain-watcher rules revalidate [--all|ID]`.**
- [ ] **7a. `domain-watcher rules revalidate --below-pipeline-version N`** —
  bulk revalidate every learned rule whose `validated_by_pipeline_version < N`.
  Test: seeds 3 rules at versions 1/1/2, runs with N=2, asserts the two
  v1 rules are revalidated (or demoted) and the v2 rule untouched.
- [ ] **7b. `domain-watcher rules learned --purge-auto --yes`** — deletes
  every row with `auto_learned=true`. Requires `--yes`; without it, the
  command exits 2 with a usage error. Test asserts row count returned.
- [ ] **8. `domain-watcher config validate PATH`** — exits 0 if valid,
  prints schema errors otherwise.
- [ ] **9. `domain-watcher version`** — prints `__version__`.
- [ ] **10. Commit per-command** as you go (`feat(cli): <command>`).

---

# Phase 10 — Composition + integration

## Task 10.1 — `composition.py`

**Files:**
- Create: `src/domain_watcher/composition.py`
- Create: `tests/integration/test_composition.py`

The single place that imports both core ports and infrastructure adapters.
Returns a fully wired `DomainWatcher`.

**Steps:**

- [ ] **1. `compose_from_config(config: Config) -> DomainWatcher`:**
  - selects state DB driver from `runtime.state_db` URL
  - instantiates each `checkers[i]` via type→factory map
  - same for `notifiers[i]`
  - builds `ParsingService` with safety gate
  - builds `EventBus` and registers reconciliation subscribers
- [ ] **2. Integration test:** load `tests/fixtures/config/valid.yaml`,
  compose, run `await watcher.check_now("example.com")` against a
  recorded RDAP fixture, assert one `DomainCheckCompleted` event.
- [ ] **3. Commit**: `feat: composition root wiring all adapters`.

## Task 10.2 — End-to-end CLI test

**Files:**
- Create: `tests/e2e/test_cli_run.py`

**Steps:**

- [ ] **1. Spawn the CLI** with a tiny config that watches one domain
  via a recorded RDAP fixture, with a `recording-notifier` (test-only
  adapter, registered via `with_notifier`). Assert the recording
  receives one alert when `FixedClock` is advanced past the threshold.
  Use `subprocess.run(["domain-watcher", "run", "--config", ...])` with
  the fixture-injected clock surfaced via env var.
- [ ] **2. Commit**: `test(e2e): full CLI run with recorded RDAP`.

## Task 10.3 — Docker

**Files:**
- Create: `docker/Dockerfile` (multi-stage; final stage `gcr.io/distroless/python3-debian12:nonroot`)
- Create: `docker/compose.yml`
- Create: `docker/example-config.yaml`
- Create: `docs/guides/operator/docker.md`

**Steps:**

- [ ] **1. Dockerfile** builds with `uv sync --frozen --no-dev` in builder
  stage; copies wheels + `/app/src` to distroless final.
- [ ] **2. `compose.yml`** with `app + ollama` services (Ollama is the
  default local LiteLLM backend; users can disable it and configure a
  cloud model via env). Named volume for the SQLite state DB.
- [ ] **3. Smoke test:** `make docker-up`, hit `domain-watcher version`
  via `docker exec`, exit cleanly.
- [ ] **4. Documentation** at `docs/guides/operator/docker.md` covers
  config mount, env vars for secrets, log volume, and how to swap the
  LiteLLM backend (`parsing.llm_fallback.suggester.settings.model`).
- [ ] **5. Commit**: `feat(docker): distroless image + compose + ops guide`.

---

## End of Chunk 4

The standalone application is now a real, runnable service: `docker
compose up` runs a daemon driven by a YAML file with hot reload, four
notification channels, three checking strategies, runtime LLM-assisted
WHOIS learning, and a CLI for ops.

Chunk 5 closes the loop on extensibility: entry-point plugin discovery
and the public test harness the bot repo (and any third-party plugin)
uses.

---

## Chunk 5 — Phase 11

# Phase 11 — Plugin protocol & release prep

## Task 11.1 — entry-point discovery

**Files:**
- Create: `src/domain_watcher/infrastructure/plugins/discovery.py`
- Create: `tests/unit/infrastructure/plugins/test_discovery.py`
- Create: `tests/integration/plugins/test_discovery_real.py`
- Create: `tests/fixtures/fake_plugin/` — a tiny installable package with
  one fake notifier registered via entry points.

**Steps:**

- [ ] **1. `discover(group, allowlist, denylist) -> dict[str, Type]`** uses
  `importlib.metadata.entry_points(group=...)` and applies filters from
  `runtime.plugins.enabled/.disabled` (ADR 0004 §5.3).
- [ ] **2. Failing tests** with a stubbed entry-point list:
  - all loaded when no filter
  - allowlist beats denylist
  - import failure surfaces with package + entry-point name in error
  - protocol-version mismatch refused (ADR 0004 §9)
- [ ] **3. Integration test:** install the fake plugin into the test venv
  (`uv pip install -e tests/fixtures/fake_plugin`), confirm `discover`
  returns it, then uninstall.
- [ ] **4. Commit**: `feat(infra/plugins): entry-point discovery with version + filter checks`.

## Task 11.2 — `domain_watcher.testing` (public test harness)

**Files:**
- Create: `src/domain_watcher/testing/__init__.py`
- Create: `src/domain_watcher/testing/clocks.py` — re-export `FixedClock`.
- Create: `src/domain_watcher/testing/repos.py` — re-export memory repos.
- Create: `src/domain_watcher/testing/contract/notifier.py` —
  `PluginContractTest` for notifiers (ADR 0004 §10).
- Create: `src/domain_watcher/testing/contract/checker.py` — same for checkers.
- Create: `src/domain_watcher/testing/contract/repo.py` — same for repos.
- Create: `tests/unit/testing/test_contracts_run_clean.py`

**Steps:**

- [ ] **1. Tests** assert that running each contract suite against the
  built-in adapters is green — this is meta-test ensuring our own
  adapters satisfy the published contracts.
- [ ] **2. Pin** `domain_watcher.testing` as a stable module: a
  semver-protected public surface like the rest of `domain_watcher`.
- [ ] **3. Commit**: `feat(testing): public contract test harness`.

## Task 11.3 — observability hooks

**Files:**
- Create: `src/domain_watcher/infrastructure/observability/structlog_setup.py`
- Create: `src/domain_watcher/infrastructure/observability/metrics.py`
- Create: `tests/unit/infrastructure/observability/test_metrics.py`
- Modify: `pyproject.toml` (`structlog>=24`, `prometheus-client>=0.20`)

**Steps:**

- [ ] **1. Structlog config** — JSON in prod, console in dev (selected
  by `runtime.log_format`). Bind `correlation_id` from current event. Include
  the `scrub_secrets` processor in the chain:
  - Redacts these keys to `"***"` (case-insensitive): `bot_token`,
    `password`, `api_key`, `smtp_password`, `secret`, `token`, `authorization`.
  - URL fields (`webhook_url`, `api_base`) are normalized to
    `scheme://host` — userinfo and query strings dropped.
  - On by default; disabling it in production is a configuration error
    and emits a startup warning.
- [ ] **2. Prometheus metrics:**
  - counter `domain_watcher_alerts_sent_total{channel,severity}`
  - counter `domain_watcher_checks_total{checker,outcome}`
  - gauge `domain_watcher_monitored_domains`
  - histogram `domain_watcher_check_duration_seconds{checker}`
  - counter `domain_watcher_rules_learned_total{tld,suggester}`
  - counter `domain_watcher_rules_invalidated_total{tld,reason}`
- [ ] **3. Mount `/metrics`** when `runtime.metrics.enabled: true` (small
  aiohttp listener; no FastAPI dep — that ADR 0001 §11(3) decision holds).
- [ ] **4. Tests** verify counters tick on the right events.
- [ ] **4a. Test scrubber** — log records with `bot_token="abc123"` emit
  `bot_token="***"`; nested dicts scrubbed; URL fields stripped.
- [ ] **5. Commit**: `feat(observability): structlog + prometheus metrics`.

## Task 11.4 — documentation polish

**Files:**
- Create: `docs/guides/operator/quickstart.md`
- Create: `docs/guides/operator/configuration.md`
- Create: `docs/guides/operator/learned-rules.md`
- Create: `docs/guides/integrator/embedding.md` — how the bot repo embeds us
- Create: `docs/reference/cli.md` — auto-generated via `typer-cli` if available
- Modify: `docs/README.md` — fill in cross-links.
- Modify: top-level `README.md`.

**Steps:**

- [ ] **1. Quickstart**: docker run + minimal config + first alert in <5min.
- [ ] **2. Configuration**: each YAML key explained with examples.
- [ ] **3. Learned-rules guide**: when to enable LLM fallback, how to
  inspect/promote/disable, threat-model summary linking ADR 0006.
- [ ] **4. Integrator guide**: explicit "how to embed `domain_watcher` in
  another async app" — code sketch matching the bot repo's needs without
  shipping bot code here.
- [ ] **5. Commit per doc**: `docs: <topic>`.

## Task 11.5 — release engineering

**Files:**
- Create: `CHANGELOG.md`
- Create: `.github/workflows/release.yml`
- Modify: `pyproject.toml` — bump version to `0.1.0`.

**Steps:**

- [ ] **1. Changelog** following Keep-a-Changelog: `0.1.0 — Initial release`
  with the feature list from the architecture overview.
- [ ] **2. Release workflow:** triggered on tag `v*`; runs full CI;
  builds wheel + sdist with `uv build`; publishes to PyPI via Trusted
  Publishing (no token in repo). Container image pushed to GHCR.
- [ ] **3. Tag dry-run** in a feature branch; verify workflow goes green
  without publishing.
- [ ] **4. Commit**: `chore(release): 0.1.0 release engineering`.

## Task 11.6 — final acceptance

- [ ] **1. Re-run all CI gates locally:**
  ```bash
  make ci            # lint + format-check + typecheck + imports-check + test
  make test-integration
  make test-e2e
  ```
- [ ] **2. Acceptance checklist against ADR 0001 §3 Goals:**
  - [ ] RDAP, WHOIS, custom-script checkers — ✓
  - [ ] Telegram (single-channel), Email, Discord, generic webhook — ✓
  - [ ] Hot-reloadable config — ✓
  - [ ] WHOIS deterministic parser + LLM fallback under safety rails — ✓
  - [ ] Plugin extensibility (entry points + explicit) — ✓
  - [ ] Clean public Python API — ✓
  - [ ] **No bot code**, no aiogram dependency — ✓
- [ ] **3. Commit**: `chore: 0.1.0 acceptance pass`.
- [ ] **4. Tag** `v0.1.0` and trigger release.

---

## End of Chunk 5

The repository now ships the full v1: library, daemon, plugin protocol,
observability, and release pipeline. The bot repository can begin
implementation against `domain_watcher==0.1.*`.