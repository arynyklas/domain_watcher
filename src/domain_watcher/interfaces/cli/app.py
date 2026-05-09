"""Typer-driven CLI: ``domain-watcher run|check|rules|config|version``.

Subcommands group by responsibility:

- ``run``                — boot the daemon foreground (long-running).
- ``check``              — one-shot: print one ``CheckResult`` as JSON.
- ``config validate``    — validate a YAML config; exit 0 if valid.
- ``rules learned`` etc. — inspect / disable / delete / revalidate
  learned WHOIS rules.
- ``version``            — print ``__version__``.

Each subcommand owns its own module under ``interfaces/cli/_*.py``; this
file is the registration root and the ``cli`` callable used by the
``domain-watcher`` console script.
"""

from __future__ import annotations

import typer

from domain_watcher.interfaces.cli._check import check_command
from domain_watcher.interfaces.cli._config import config_app
from domain_watcher.interfaces.cli._rules import rules_app
from domain_watcher.interfaces.cli._run import run_command
from domain_watcher.interfaces.cli._version import version_command

cli = typer.Typer(
    name="domain-watcher",
    help="Periodic domain expiration checker.",
    no_args_is_help=True,
    add_completion=False,
)

cli.command(name="run", help="Run the daemon in the foreground.")(run_command)
cli.command(
    name="check",
    help="One-shot check; prints the result as JSON.",
)(check_command)
cli.command(name="version", help="Print the package version.")(version_command)
cli.add_typer(config_app, name="config", help="Configuration utilities.")
cli.add_typer(rules_app, name="rules", help="Learned-rule administration.")


def main() -> None:  # pragma: no cover — entrypoint
    cli()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["cli", "main"]
