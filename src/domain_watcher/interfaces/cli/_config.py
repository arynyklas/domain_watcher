"""``domain-watcher config validate PATH`` — exit 0 on success.

On failure, prints the validator's error message to stderr and exits
with code 1. Useful as a pre-deploy gate (``domain-watcher config
validate /etc/domain-watcher/config.yaml || exit 1``).
"""

from __future__ import annotations

from pathlib import Path

import typer

from domain_watcher.core.shared.errors import ConfigError
from domain_watcher.infrastructure.config.loader import load_config

config_app = typer.Typer(
    name="config",
    help="Configuration utilities.",
    no_args_is_help=True,
    add_completion=False,
)


@config_app.command("validate")
def validate(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
) -> None:
    """Validate the YAML at ``PATH`` and exit 0 if it loads cleanly."""
    try:
        load_config(path)
    except ConfigError as exc:
        typer.echo(f"invalid config: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("ok")


__all__ = ["config_app"]
