"""YAML + env interpolation loader tests (ADR 0003 §2, §3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from domain_watcher.core.shared.errors import ConfigError
from domain_watcher.infrastructure.config.loader import (
    interpolate_env,
    load_config,
    resolve_config_path,
)

_FIXTURES = Path(__file__).parents[3] / "fixtures" / "config"


# ---------------------------------------------------------------------------
# interpolate_env
# ---------------------------------------------------------------------------
def test_interpolate_simple() -> None:
    out = interpolate_env({"token": "${X}"}, env={"X": "abc"})
    assert out == {"token": "abc"}


def test_interpolate_default_when_unset() -> None:
    out = interpolate_env({"token": "${MISSING:-fallback}"}, env={})
    assert out == {"token": "fallback"}


def test_interpolate_default_overridden_by_set() -> None:
    out = interpolate_env({"token": "${X:-fallback}"}, env={"X": "real"})
    assert out == {"token": "real"}


def test_interpolate_unset_required_raises() -> None:
    with pytest.raises(ConfigError) as exc:
        interpolate_env({"token": "${REQUIRED}"}, env={})
    assert "REQUIRED" in str(exc.value)


def test_interpolate_recurses_lists_and_nested_dicts() -> None:
    out = interpolate_env(
        {
            "outer": [
                {"k": "${A}"},
                "literal",
                ["${B}", 42],
            ],
        },
        env={"A": "alpha", "B": "beta"},
    )
    assert out == {
        "outer": [
            {"k": "alpha"},
            "literal",
            ["beta", 42],
        ],
    }


def test_interpolate_multiple_refs_in_one_string() -> None:
    out = interpolate_env(
        "${A}_and_${B:-bb}_and_${C}",
        env={"A": "aa", "C": "cc"},
    )
    assert out == "aa_and_bb_and_cc"


def test_interpolate_default_with_empty_string() -> None:
    out = interpolate_env("${X:-}", env={})
    assert out == ""


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------
def test_load_valid_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_BOT_TOKEN", "tok")
    monkeypatch.setenv("TG_OPS_CHAT", "12345")
    monkeypatch.setenv("PD_TOKEN", "pd-secret")
    cfg = load_config(_FIXTURES / "valid.yaml")
    assert cfg.version == 1
    # secrets ARE interpolated:
    tg = next(n for n in cfg.notifiers if n.id == "tg-ops")
    assert tg.settings["bot_token"] == "tok"


def test_load_unresolved_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REQUIRED_BUT_MISSING_TOKEN", raising=False)
    with pytest.raises(ConfigError) as exc:
        load_config(_FIXTURES / "unresolved-env.yaml")
    assert "REQUIRED_BUT_MISSING_TOKEN" in str(exc.value)


def test_load_missing_checker_reference_raises() -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(_FIXTURES / "missing-checker.yaml")
    assert "rdao" in str(exc.value)


def test_load_bad_cron_raises() -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(_FIXTURES / "bad-cron.yaml")
    msg = str(exc.value)
    assert "cron" in msg or "schedule" in msg


def test_load_dup_id_raises() -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(_FIXTURES / "dup-id.yaml")
    assert "rdap" in str(exc.value)


def test_load_nonexistent_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path / "no-such-file.yaml")
    assert "cannot read" in str(exc.value)


def test_load_empty_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "empty" in str(exc.value)


def test_load_top_level_must_be_mapping(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "mapping" in str(exc.value)


def test_load_yaml_parse_error(tmp_path: Path) -> None:
    p = tmp_path / "broken.yaml"
    p.write_text("foo: [unclosed\n")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert "YAML parse" in str(exc.value)


# ---------------------------------------------------------------------------
# resolve_config_path precedence
# ---------------------------------------------------------------------------
def test_resolve_cli_path_wins(tmp_path: Path) -> None:
    p = tmp_path / "explicit.yaml"
    p.write_text("version: 1\n")
    assert resolve_config_path(cli_path=p) == p


def test_resolve_cli_path_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        resolve_config_path(cli_path=tmp_path / "missing.yaml")
    assert "--config" in str(exc.value)


def test_resolve_env_path(tmp_path: Path) -> None:
    p = tmp_path / "env.yaml"
    p.write_text("version: 1\n")
    assert resolve_config_path(env={"DOMAIN_WATCHER_CONFIG": str(p)}) == p


def test_resolve_env_path_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        resolve_config_path(env={"DOMAIN_WATCHER_CONFIG": str(tmp_path / "nope.yaml")})
    assert "DOMAIN_WATCHER_CONFIG" in str(exc.value)


def test_resolve_default_path_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "domain-watcher.yaml"
    p.write_text("version: 1\n")
    # No env, no XDG → falls through to ./domain-watcher.yaml
    out = resolve_config_path(env={"XDG_CONFIG_HOME": str(tmp_path / "xdg-empty")})
    assert out.resolve() == p.resolve()


def test_resolve_xdg_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # ensure ./domain-watcher.yaml does not exist
    xdg = tmp_path / "xdg"
    target = xdg / "domain-watcher" / "config.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("version: 1\n")
    out = resolve_config_path(env={"XDG_CONFIG_HOME": str(xdg)})
    assert out == target


def test_resolve_no_candidate_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as exc:
        resolve_config_path(env={"XDG_CONFIG_HOME": str(tmp_path / "xdg-empty")})
    assert "no configuration file" in str(exc.value)
