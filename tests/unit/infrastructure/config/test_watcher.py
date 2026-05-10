"""ConfigFileWatcher: load → validate → ConfigHolder.update with debounce.

Tests are split between:
  - **logic** tests that drive ``ConfigFileWatcher`` through its asyncio
    seam directly (no real fs events). They cover loader/holder wiring,
    debounce collapsing, and error isolation.
  - **integration** tests that use a real ``watchdog.observers.Observer``
    against ``tmp_path``. Watchdog is thread-based and can flake under
    fast filesystems, so these tests include explicit polling with a
    bounded timeout.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain_watcher.application.use_cases.reload_config import ConfigHolder
from domain_watcher.core.shared.errors import ConfigError
from domain_watcher.infrastructure.config.watcher import ConfigFileWatcher

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


@dataclass(frozen=True)
class _StubConfig:
    """Config-shaped stub so we don't drag the full schema into watcher tests."""

    version: int
    label: str = "v0"


# ---------------------------------------------------------------------------
# Logic-level tests (no real watchdog observer)
# ---------------------------------------------------------------------------
async def test_reload_calls_loader_and_updates_holder(tmp_path: Path) -> None:
    p = tmp_path / "cfg.yaml"
    p.write_text("dummy: 1\n")
    seen_paths: list[Path] = []

    def loader(path: Path) -> _StubConfig:
        seen_paths.append(path)
        return _StubConfig(version=1, label="loaded")

    holder: ConfigHolder[_StubConfig] = ConfigHolder()
    watcher = ConfigFileWatcher(p, loader, holder, debounce_seconds=0.0)

    await watcher.trigger_reload_for_tests()
    assert holder.current == _StubConfig(version=1, label="loaded")
    assert seen_paths == [p.resolve()]


async def test_validation_failure_keeps_old_config(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    p = tmp_path / "cfg.yaml"
    p.write_text("ignored: true\n")

    holder: ConfigHolder[_StubConfig] = ConfigHolder(
        initial=_StubConfig(version=1, label="orig")
    )

    calls = {"n": 0}

    def loader(_: Path) -> _StubConfig:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConfigError("invalid: domain[0].checker references unknown id 'rdao'")
        return _StubConfig(version=1, label="recovered")

    watcher = ConfigFileWatcher(p, loader, holder, debounce_seconds=0.0)

    with caplog.at_level(
        logging.ERROR, logger="domain_watcher.infrastructure.config.watcher"
    ):
        await watcher.trigger_reload_for_tests()
    assert holder.current == _StubConfig(version=1, label="orig")
    assert any(
        "config reload failed" in r.message and "rdao" in r.message
        for r in caplog.records
    )

    # Successful retry replaces the config.
    await watcher.trigger_reload_for_tests()
    assert holder.current == _StubConfig(version=1, label="recovered")


async def test_unexpected_loader_error_isolated(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    p = tmp_path / "cfg.yaml"
    p.write_text("x: 1\n")

    holder: ConfigHolder[_StubConfig] = ConfigHolder(initial=_StubConfig(version=1))

    def loader(_: Path) -> _StubConfig:
        raise RuntimeError("disk on fire")

    watcher = ConfigFileWatcher(p, loader, holder, debounce_seconds=0.0)

    with caplog.at_level(
        logging.ERROR, logger="domain_watcher.infrastructure.config.watcher"
    ):
        await watcher.trigger_reload_for_tests()  # MUST NOT raise
    assert holder.current == _StubConfig(version=1)
    assert any("config reload raised unexpectedly" in r.message for r in caplog.records)


async def test_debounce_collapses_burst(tmp_path: Path) -> None:
    """Multiple rapid `_schedule_reload` calls produce exactly one reload."""
    p = tmp_path / "cfg.yaml"
    p.write_text("y: 1\n")

    call_count = {"n": 0}

    def loader(_: Path) -> _StubConfig:
        call_count["n"] += 1
        return _StubConfig(version=1, label=f"v{call_count['n']}")

    holder: ConfigHolder[_StubConfig] = ConfigHolder()
    watcher = ConfigFileWatcher(p, loader, holder, debounce_seconds=0.05)

    await watcher.start()
    try:
        # Fire 5 events within the debounce window.
        for _ in range(5):
            watcher._on_change()  # bridges to asyncio
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.15)  # past debounce
    finally:
        await watcher.stop()

    assert call_count["n"] == 1
    assert holder.current == _StubConfig(version=1, label="v1")


# ---------------------------------------------------------------------------
# Real-watchdog integration (still in the unit suite — uses tmp_path only).
# Watchdog is thread-based; we poll with a generous timeout to avoid flakes.
# ---------------------------------------------------------------------------
async def _wait_until(predicate: Callable[[], bool], *, timeout: float) -> bool:  # noqa: ASYNC109
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def test_real_observer_picks_up_modify(tmp_path: Path) -> None:
    p = tmp_path / "cfg.yaml"
    p.write_text("first: 1\n")

    versions: list[str] = []

    def loader(path: Path) -> _StubConfig:
        text = path.read_text().strip()
        versions.append(text)
        return _StubConfig(version=1, label=text)

    holder: ConfigHolder[_StubConfig] = ConfigHolder()
    watcher = ConfigFileWatcher(p, loader, holder, debounce_seconds=0.05)
    await watcher.start()
    try:
        # Editor save: rewrite content.
        p.write_text("second: 2\n")
        ok = await _wait_until(
            lambda: holder.current is not None and holder.current.label == "second: 2",
            timeout=2.0,
        )
        assert ok, f"holder did not update; saw versions={versions}"
    finally:
        await watcher.stop()


async def test_real_observer_handles_atomic_replace(tmp_path: Path) -> None:
    """Editor 'atomic save' = write tmp + rename over target. Must reload."""
    target = tmp_path / "cfg.yaml"
    target.write_text("v: 0\n")

    holder: ConfigHolder[_StubConfig] = ConfigHolder()
    seen: list[str] = []

    def loader(path: Path) -> _StubConfig:
        text = path.read_text().strip()
        seen.append(text)
        return _StubConfig(version=1, label=text)

    watcher = ConfigFileWatcher(target, loader, holder, debounce_seconds=0.05)
    await watcher.start()
    try:
        tmp = tmp_path / ".cfg.yaml.swp"
        tmp.write_text("v: replaced\n")
        tmp.replace(target)  # atomic move-over
        ok = await _wait_until(
            lambda: (
                holder.current is not None and holder.current.label == "v: replaced"
            ),
            timeout=2.0,
        )
        assert ok, f"holder did not pick up replace; seen={seen}"
    finally:
        await watcher.stop()
