"""YAML loader with env-var interpolation and file-precedence resolution.

Per ADR 0003 §2:

  1. ``--config <path>`` CLI flag (caller's responsibility — passed in)
  2. ``DOMAIN_WATCHER_CONFIG`` env var
  3. ``./domain-watcher.yaml``
  4. ``/etc/domain-watcher/config.yaml``
  5. ``$XDG_CONFIG_HOME/domain-watcher/config.yaml``

Per ADR 0003 §3:

  - ``${NAME}`` expands to the env var value; missing → ``ConfigError``.
  - ``${NAME:-default}`` falls back to ``default`` when unset.

Interpolation runs **after** YAML parse on string leaves only — keeps
``${var}`` inside multi-line strings intact even when YAML quoting would
otherwise lose them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from domain_watcher.core.shared.errors import ConfigError
from domain_watcher.infrastructure.config.schema import Config

if TYPE_CHECKING:
    from collections.abc import Mapping


_ENV_REF_RE = re.compile(
    r"""
    \$\{                       # opening ${
    (?P<name>[A-Z_][A-Z0-9_]*) # var name — uppercase only, by convention.
                               # Lowercase ${var} forms are reserved for
                               # webhook body_template placeholders.
    (?::-(?P<default>[^}]*))?  # optional :- default
    \}                         # closing }
    """,
    re.VERBOSE,
)


_DEFAULT_LOCATIONS: tuple[Path, ...] = (
    Path("./domain-watcher.yaml"),
    Path("/etc/domain-watcher/config.yaml"),
)


def resolve_config_path(
    *,
    cli_path: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the configuration file path per ADR 0003 §2.

    Precedence: ``cli_path`` > ``DOMAIN_WATCHER_CONFIG`` env > ``./domain-watcher.yaml``
    > ``/etc/domain-watcher/config.yaml`` > ``$XDG_CONFIG_HOME/domain-watcher/config.yaml``.

    Raises ``ConfigError`` if no candidate exists.
    """
    e = os.environ if env is None else env

    if cli_path is not None:
        p = Path(cli_path)
        if not p.exists():
            raise ConfigError(f"config file from --config does not exist: {p}")
        return p

    env_path = e.get("DOMAIN_WATCHER_CONFIG")
    if env_path:
        p = Path(env_path)
        if not p.exists():
            raise ConfigError(f"config file from DOMAIN_WATCHER_CONFIG does not exist: {p}")
        return p

    for candidate in _DEFAULT_LOCATIONS:
        if candidate.exists():
            return candidate

    xdg = e.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    xdg_path = Path(xdg) / "domain-watcher" / "config.yaml"
    if xdg_path.exists():
        return xdg_path

    raise ConfigError(
        "no configuration file found; tried --config, DOMAIN_WATCHER_CONFIG, "
        f"{_DEFAULT_LOCATIONS}, and {xdg_path}"
    )


def interpolate_env(node: object, env: Mapping[str, str] | None = None) -> object:
    """Recursively replace ``${VAR}`` / ``${VAR:-default}`` in string leaves.

    Lists and dicts are walked. Non-string scalars are returned unchanged.
    Raises ``ConfigError`` if a referenced var has no value and no default.
    """
    e = os.environ if env is None else env

    if isinstance(node, str):
        return _interpolate_string(node, e)
    if isinstance(node, list):
        return [interpolate_env(item, e) for item in node]
    if isinstance(node, dict):
        return {k: interpolate_env(v, e) for k, v in node.items()}
    return node


def _interpolate_string(s: str, env: Mapping[str, str]) -> str:
    def _sub(m: re.Match[str]) -> str:
        name = m.group("name")
        default = m.group("default")
        if name in env:
            return env[name]
        if default is not None:
            return default
        raise ConfigError(f"env var {name!r} is unresolved (referenced in config)")

    return _ENV_REF_RE.sub(_sub, s)


def load_config(
    path: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None = None,
) -> Config:
    """Read ``path``, interpolate env, validate, return a frozen ``Config``.

    Failures: ``ConfigError`` for I/O / unresolved env / validation issues.
    The original Pydantic / YAML / OSError is preserved as ``__cause__``.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {p}: {exc}") from exc

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error in {p}: {exc}") from exc

    if parsed is None:
        raise ConfigError(f"config file {p} is empty")
    if not isinstance(parsed, dict):
        raise ConfigError(
            f"config file {p} top-level must be a mapping, got {type(parsed).__name__}"
        )

    interpolated = interpolate_env(parsed, env)

    try:
        return Config.model_validate(interpolated)
    except ValidationError as exc:
        raise ConfigError(f"config validation failed for {p}:\n{exc}") from exc


__all__ = [
    "interpolate_env",
    "load_config",
    "resolve_config_path",
]
