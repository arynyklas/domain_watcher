"""Root pytest hooks.

Pytest exits with code 5 when no tests are collected. During very early
project state (Phase 0, before any unit tests exist) `make test-unit`
runs `pytest tests/unit` against an empty tree. We treat "no tests
collected" as success — a missing-tests gate belongs in CI, not the
Makefile recipe.
"""

from __future__ import annotations


def pytest_sessionfinish(session, exitstatus: int) -> None:
    if exitstatus == 5:
        session.exitstatus = 0
