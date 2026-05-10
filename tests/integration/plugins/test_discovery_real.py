"""Real entry-point discovery against a temporarily-installed fake plugin.

The fake plugin lives at ``tests/fixtures/fake_plugin``. The test installs
it into the active venv with ``uv pip install -e``, runs the discover()
loader **in a subprocess** (so Python's site-packages scan picks the
new package up cleanly), then uninstalls. ``pytest.mark.integration``
keeps it out of the default unit-test run because it shells out to
``uv``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from domain_watcher.infrastructure.plugins.discovery import PluginLoadError

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "fake_plugin"


def _uv_available() -> bool:
    return shutil.which("uv") is not None


def _pip_install(target: str) -> None:
    subprocess.run(
        ["uv", "pip", "install", "--python", sys.executable, target],
        check=True,
    )


def _pip_uninstall(name: str) -> None:
    subprocess.run(
        ["uv", "pip", "uninstall", "--python", sys.executable, name],
        check=True,
    )


def _run_discover(group: str, *, disabled: list[str] | None = None) -> dict[str, str]:
    """Spawn a subprocess that calls ``discover()`` and prints JSON.

    A fresh interpreter sees freshly-installed site-packages without
    needing to reload Python's path hooks.
    """

    code = textwrap.dedent(
        f"""
        import json
        from domain_watcher.infrastructure.plugins.discovery import discover

        out = discover({group!r}, disabled={(disabled or [])!r})
        print(json.dumps({{name: cls.__name__ for name, cls in out.items()}}))
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_discover_loads_real_fake_plugin() -> None:
    if not _uv_available():
        pytest.skip("uv binary unavailable in PATH")
    if not FIXTURE_DIR.exists():
        pytest.skip(f"fixture {FIXTURE_DIR} missing")

    _pip_install(f"-e{FIXTURE_DIR}")
    try:
        out = _run_discover("domain_watcher.notifiers")
    finally:
        _pip_uninstall("fake-dw-plugin")

    assert out.get("fake") == "FakeNotifier", f"discovered: {out}"


def test_real_discover_filters_apply() -> None:
    if not _uv_available():
        pytest.skip("uv binary unavailable in PATH")
    if not FIXTURE_DIR.exists():
        pytest.skip(f"fixture {FIXTURE_DIR} missing")

    _pip_install(f"-e{FIXTURE_DIR}")
    try:
        # disabled list MUST drop the plugin even though the protocol matches.
        out = _run_discover("domain_watcher.notifiers", disabled=["fake"])
    finally:
        _pip_uninstall("fake-dw-plugin")

    assert "fake" not in out, f"discovered: {out}"


# A static check, no install required: PluginLoadError is exported and
# carries the documented attributes.
def test_plugin_load_error_carries_metadata() -> None:
    err = PluginLoadError(group="g", name="n", dist="d", reason="r")
    assert err.group == "g"
    assert err.name == "n"
    assert err.dist == "d"
    assert err.reason == "r"
    assert "d:g:n" in str(err)
