"""Tests for the typer CLI (Task 9.3).

Uses ``typer.testing.CliRunner`` so the same code paths a subprocess
would exercise are run in-process. End-to-end subprocess coverage lives
in ``tests/e2e/test_cli_run.py``.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from domain_watcher import __version__
from domain_watcher.interfaces.cli.app import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def env_for_valid_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the env vars referenced by ``tests/fixtures/config/valid.yaml``."""
    monkeypatch.setenv("TG_BOT_TOKEN", "abc123")
    monkeypatch.setenv("TG_OPS_CHAT", "-100200")
    monkeypatch.setenv("PD_TOKEN", "pd-token")


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def test_version_prints_module_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


# ---------------------------------------------------------------------------
# config validate
# ---------------------------------------------------------------------------


def test_config_validate_ok(
    runner: CliRunner,
    env_for_valid_yaml: None,
) -> None:
    fixture = Path("tests/fixtures/config/valid.yaml")
    result = runner.invoke(cli, ["config", "validate", str(fixture)])
    assert result.exit_code == 0
    assert "ok" in result.stdout


def test_config_validate_rejects_missing_checker(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    yaml_text = textwrap.dedent(
        """\
        version: 1
        checkers:
          - id: rdap
            type: rdap
        notifiers:
          - id: tg
            type: telegram
            settings:
              bot_token: x
              chat_id: y
        domains:
          - name: example.com
            checker: nonexistent
            schedule: "0 * * * *"
            channels: [tg]
        """
    )
    config = tmp_path / "config.yaml"
    config.write_text(yaml_text)
    result = runner.invoke(cli, ["config", "validate", str(config)])
    assert result.exit_code == 1
    assert "checker" in result.output.lower()


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@pytest.fixture
def script_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a config that uses the ``script`` checker hitting a fixture script."""
    script = Path("tests/fixtures/scripts/ok.sh").resolve()
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
    return config


def test_check_runs_one_shot_against_script_checker(
    runner: CliRunner,
    script_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ok.sh prints a success JSON for any FQDN
    result = runner.invoke(
        cli,
        ["check", "example.com", "--checker", "script", "--config", str(script_config)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["domain"] == "example.com"
    assert payload["outcome"] == "ok"
    assert payload["expires_at"] is not None
    assert payload["source"] == "script"


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


def test_rules_learned_lists_empty_initially(
    runner: CliRunner, script_config: Path
) -> None:
    result = runner.invoke(cli, ["rules", "learned", "--config", str(script_config)])
    assert result.exit_code == 0
    assert "no learned rules" in result.output.lower()


def test_rules_purge_auto_requires_yes(runner: CliRunner, script_config: Path) -> None:
    # --purge-auto without --yes is an explicit usage error (exit 2).
    result = runner.invoke(
        cli,
        ["rules", "learned", "--purge-auto", "--config", str(script_config)],
    )
    assert result.exit_code == 2
    assert "--yes" in result.output


def test_rules_revalidate_no_target_errors(
    runner: CliRunner, script_config: Path
) -> None:
    result = runner.invoke(
        cli,
        ["rules", "revalidate", "--config", str(script_config)],
    )
    assert result.exit_code == 2


def test_rules_show_unknown_id(runner: CliRunner, script_config: Path) -> None:
    result = runner.invoke(
        cli,
        ["rules", "show", "9999", "--config", str(script_config)],
    )
    assert result.exit_code == 1
    assert "no rule" in result.output.lower()
