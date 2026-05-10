# domain-watcher — developer commands
# Convention: every target is .PHONY; tab-indented recipes; `make help` lists them.
SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help install sync test test-unit test-integration test-e2e \
        lint format format-check typecheck imports-check migrations-check \
        check ci clean run docker-build docker-up docker-down

help:  ## list targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## install runtime + dev deps into the project venv
	$(UV) sync --extra dev

sync: install  ## alias for install

lint:  ## ruff lint
	$(UV) run ruff check --fix

format:  ## ruff auto-format
	$(UV) run ruff format

format-check:  ## ruff format check (no writes)
	$(UV) run ruff format --check

typecheck:  ## ty type-check (mypy successor)
	$(UV) run ty check

imports-check:  ## verify layered architecture rules
	$(UV) run lint-imports

migrations-check:  ## verify alembic models match migrations (no drift)
	rm -f state.db
	$(UV) run --no-sync alembic upgrade head
	$(UV) run --no-sync alembic check
	rm -f state.db

test: test-unit  ## default = unit tests only

test-unit:  ## fast unit tests (no I/O)
	$(UV) run pytest -q tests/unit tests/contracts

test-integration:  ## integration tests (Docker required)
	$(UV) run pytest -q -m integration

test-e2e:  ## end-to-end tests
	$(UV) run pytest -q tests/e2e

test-all: test-unit test-integration test-e2e  ## every test suite

check: lint format-check typecheck imports-check test-unit  ## local pre-commit gate

# Coverage in `addopts` (pyproject.toml) regenerates `coverage.xml` from
# cumulative `.coverage` data on every pytest run; passing
# `--cov-append` to the second invocation gives us a unit + e2e union
# in one report.
ci: lint format-check typecheck imports-check migrations-check  ## what CI runs
	rm -f .coverage coverage.xml
	$(UV) run pytest -q tests/unit tests/contracts
	$(UV) run pytest -q --cov-append tests/e2e

clean:  ## remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .ty_cache .mypy_cache dist build htmlcov .coverage* coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +

run:  ## run the daemon against ./domain-watcher.yaml
	$(UV) run domain-watcher run --config ./domain-watcher.yaml

docker-build:  ## build the docker image
	DOCKER_BUILDKIT=1 docker build -t domain-watcher:dev -f docker/Dockerfile .

docker-up:  ## start docker compose stack
	docker compose -f docker/compose.yml up -d

docker-down:  ## stop docker compose stack
	docker compose -f docker/compose.yml down
