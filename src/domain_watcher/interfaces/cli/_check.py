"""``domain-watcher check DOMAIN [--checker ID]`` — one-shot check.

Loads the YAML config, composes a ``DomainWatcher``, ensures the
domain is being watched (idempotent upsert), runs ``check_now``, and
prints the result as JSON.

Exit codes:

- 0  → ``CheckResult.outcome == OK``
- 1  → ``PERMANENT_ERROR``
- 2  → ``TRANSIENT_ERROR`` (after retries)
- 3  → unhandled exception (with traceback to stderr)

JSON shape::

  {"domain": "...", "outcome": "...", "expires_at": "...",
   "source": "...", "error": "..."}
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer

from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.monitoring.value_objects import ChannelId
from domain_watcher.core.shared.errors import ConfigError, DomainWatcherError
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.config.loader import load_config, resolve_config_path


def check_command(
    domain: str = typer.Argument(..., help="FQDN to check (e.g. example.com)."),
    checker: str | None = typer.Option(
        None,
        "--checker",
        "-c",
        help="Checker id from config; default: first configured checker.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to the YAML config; defaults follow ADR 0003 §2 search order.",
    ),
) -> None:
    """Run one check and print the result as JSON."""
    try:
        cfg_path = resolve_config_path(cli_path=config)
        cfg = load_config(cfg_path)
    except ConfigError as exc:
        typer.echo(f"config error: {exc}", err=True)
        raise typer.Exit(code=3) from None

    # Deferred composition import keeps `check` snappy even when the config
    # tree is broken (we still want to surface the load error first).
    from domain_watcher.composition import compose_from_config  # noqa: PLC0415

    watcher = compose_from_config(cfg)
    chosen_checker = (
        checker
        if checker is not None
        else next(iter(c.id for c in watcher.checker_registry.all()), None)
    )
    if chosen_checker is None:
        typer.echo("no checkers configured", err=True)
        raise typer.Exit(code=3)
    if chosen_checker not in watcher.checker_registry:
        typer.echo(
            f"unknown checker {chosen_checker!r}; "
            f"known: {sorted(c.id for c in watcher.checker_registry.all())}",
            err=True,
        )
        raise typer.Exit(code=3)

    domain_name = DomainName(domain)

    async def _run() -> int:
        await watcher.start()
        try:
            existing = await watcher.repo.get(domain_name)
            if existing is None or existing.checker_id != chosen_checker:
                # ensure_watching: pick the first configured channel id so
                # the aggregate's invariants are satisfied.
                channel_ids = sorted(n.id for n in watcher.notifier_registry.all())
                if not channel_ids:
                    typer.echo("no notifiers configured", err=True)
                    return 3
                await watcher.ensure_watching(
                    domain_name,
                    checker_id=chosen_checker,
                    channels=[ChannelId(channel_ids[0])],
                )
            result = await watcher.check_now(domain_name)
        finally:
            await watcher.stop()

        payload = {
            "domain": result.domain.value,
            "outcome": result.outcome.value,
            "expires_at": (
                result.expires_at.isoformat() if result.expires_at is not None else None
            ),
            "source": result.source,
            "error": result.error,
        }
        typer.echo(json.dumps(payload))
        if result.outcome is CheckOutcome.OK:
            return 0
        if result.outcome is CheckOutcome.PERMANENT_ERROR:
            return 1
        return 2

    try:
        code = asyncio.run(_run())
    except DomainWatcherError as exc:
        typer.echo(f"check failed: {exc}", err=True)
        raise typer.Exit(code=3) from None
    except Exception:
        import traceback  # noqa: PLC0415 — only loaded on the unhandled-error path

        traceback.print_exc(file=sys.stderr)
        raise typer.Exit(code=3) from None
    raise typer.Exit(code=code)


__all__ = ["check_command"]
