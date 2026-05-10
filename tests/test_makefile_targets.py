"""Guard against the plan and the Makefile drifting apart.

Update EXPECTED_TARGETS together with the Makefile when adding new targets.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

EXPECTED_TARGETS = {
    "help",
    "install",
    "sync",
    "lint",
    "format",
    "format-check",
    "typecheck",
    "imports-check",
    "migrations-check",
    "test",
    "test-unit",
    "test-integration",
    "test-e2e",
    "test-all",
    "check",
    "ci",
    "clean",
    "run",
    "docker-build",
    "docker-up",
    "docker-down",
}

REPO_ROOT = Path(__file__).resolve().parent.parent


def _parse_help_targets() -> set[str]:
    out = subprocess.run(
        ["make", "help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # `make help` prints lines like "  \033[36mhelp              \033[0m list targets"
    pattern = re.compile(r"\x1b\[36m\s*([a-zA-Z0-9_-]+)\s*\x1b\[0m")
    return {m.group(1) for m in pattern.finditer(out)}


def test_makefile_help_lists_exactly_expected_targets() -> None:
    assert _parse_help_targets() == EXPECTED_TARGETS
