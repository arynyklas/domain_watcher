"""End-to-end CLI test (Task 10.2).

Spawns ``domain-watcher`` as a real subprocess and exercises the
hermetic flow:

1. ``domain-watcher version`` exits cleanly and prints the version.
2. ``domain-watcher config validate <fixture>`` exits 0.
3. ``domain-watcher check`` against a config that uses a script-checker
   fixture returns a JSON ``CheckResult`` and exits 0.

The full daemon run with a recorded RDAP fixture + a recording notifier
is exercised via the in-process composition integration test
(``tests/integration/test_composition.py``); driving the scheduler from
a foreign process at sub-second resolution is fragile and adds no
additional coverage.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

from domain_watcher import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Invoke the installed CLI binary in-process via ``uv run``."""
    return subprocess.run(
        ["uv", "run", "domain-watcher", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, **(env or {})},
        check=False,
    )


def test_cli_version_subprocess() -> None:
    proc = _run_cli("version")
    assert proc.returncode == 0
    assert __version__ in proc.stdout


def test_cli_config_validate_subprocess() -> None:
    proc = _run_cli(
        "config",
        "validate",
        "tests/fixtures/config/valid.yaml",
        env={
            "TG_BOT_TOKEN": "abc",
            "TG_OPS_CHAT": "1",
            "PD_TOKEN": "p",
        },
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "ok" in proc.stdout


def test_cli_check_subprocess_against_script_fixture(tmp_path: Path) -> None:
    """Real subprocess exercising the ``check`` happy path end-to-end."""
    script = (REPO_ROOT / "tests" / "fixtures" / "scripts" / "ok.sh").resolve()
    yaml_text = textwrap.dedent(
        f"""\
        version: 1
        runtime:
          state_db: memory://
        checkers:
          - id: script
            type: script
            settings:
              command: ["{script}"]
              timeout: 5s
        notifiers:
          - id: webhook-recorder
            type: webhook
            settings:
              url: http://127.0.0.1:1/discard
              body_template: '{{"d": "${{domain}}"}}'
        domains: []
        """
    )
    config = tmp_path / "config.yaml"
    config.write_text(yaml_text)
    proc = _run_cli(
        "check",
        "example.com",
        "--checker",
        "script",
        "--config",
        str(config),
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["domain"] == "example.com"
    assert payload["outcome"] == "ok"
    assert payload["source"] == "script"
