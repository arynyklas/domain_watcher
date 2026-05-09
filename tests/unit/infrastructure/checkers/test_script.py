"""ScriptChecker: ok, transient, bad-json, timeout, spawn failure."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.checkers.script import ScriptChecker

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "scripts"


def _bash(script: str) -> tuple[str, ...]:
    return ("/usr/bin/env", "bash", str(FIXTURES / script))


async def test_ok_script_returns_ok() -> None:
    checker = ScriptChecker(command=_bash("ok.sh"), timeout=5.0)
    result = await checker.check(DomainName("example.com"))
    assert result.outcome is CheckOutcome.OK
    assert result.expires_at == datetime(2027, 1, 1, tzinfo=UTC)


async def test_transient_script_returns_transient() -> None:
    checker = ScriptChecker(command=_bash("transient.sh"), timeout=5.0)
    result = await checker.check(DomainName("example.com"))
    assert result.outcome is CheckOutcome.TRANSIENT_ERROR
    assert "registry timeout" in (result.error or "")


async def test_bad_json_returns_permanent() -> None:
    checker = ScriptChecker(command=_bash("bad-json.sh"), timeout=5.0)
    result = await checker.check(DomainName("example.com"))
    assert result.outcome is CheckOutcome.PERMANENT_ERROR


async def test_timeout_kills_subprocess() -> None:
    checker = ScriptChecker(command=_bash("slow.sh"), timeout=0.1)
    result = await checker.check(DomainName("example.com"))
    assert result.outcome is CheckOutcome.TRANSIENT_ERROR
    assert "timeout" in (result.error or "")


async def test_spawn_failure_permanent() -> None:
    checker = ScriptChecker(command=("/nonexistent/binary",), timeout=1.0)
    result = await checker.check(DomainName("example.com"))
    assert result.outcome is CheckOutcome.PERMANENT_ERROR


_ = pytest
