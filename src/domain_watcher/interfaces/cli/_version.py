"""``domain-watcher version`` — print ``__version__``."""

from __future__ import annotations

import typer

from domain_watcher import __version__


def version_command() -> None:
    """Print the package version on stdout."""
    typer.echo(__version__)


__all__ = ["version_command"]
