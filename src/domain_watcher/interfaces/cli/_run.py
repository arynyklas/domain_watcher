"""``domain-watcher run --config PATH`` — foreground daemon.

Boots the composed ``DomainWatcher``, registers SIGINT/SIGTERM handlers,
and waits until the operator hits Ctrl-C. The watchdog-based config
reload watcher is wired here so the daemon process picks up YAML edits
without a restart (Phase 7); subscribers are reconciliation-aware
(Phase 7 Task 7.4) — but in v1 of the standalone app we only re-bind
the scheduled domain set on reload.

A failed reload never crashes the daemon: ``ConfigFileWatcher``
swallows config errors and keeps the previous good config (ADR 0003 §6).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Iterable
from pathlib import Path

import typer

from domain_watcher.application.scheduling import JobCallable
from domain_watcher.application.use_cases.reload_config import (
    ConfigHolder,
    SchedulerSubscriber,
)
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.shared.errors import ConfigError
from domain_watcher.infrastructure.config.loader import load_config, resolve_config_path
from domain_watcher.infrastructure.config.schema import Config
from domain_watcher.infrastructure.config.watcher import ConfigFileWatcher

_log = logging.getLogger(__name__)


def run_command(
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to the YAML config; default search order per ADR 0003 §2.",
    ),
    no_reload: bool = typer.Option(
        False, "--no-reload", help="Disable the config-file hot-reload watcher."
    ),
    log_level: str = typer.Option("INFO", "--log-level", help="Python logging level."),
) -> None:
    """Run the daemon foreground until SIGINT / SIGTERM."""
    logging.basicConfig(level=log_level.upper())

    try:
        cfg_path = resolve_config_path(cli_path=config)
        cfg = load_config(cfg_path)
    except ConfigError as exc:
        typer.echo(f"config error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    # Compose deferred so the heavy import doesn't penalise --help / version.
    from domain_watcher.composition import compose_from_config

    watcher = compose_from_config(cfg)

    async def _serve() -> None:
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _on_signal(*_a: object) -> None:
            _log.info("stop signal received; shutting down")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _on_signal)
            except (NotImplementedError, RuntimeError):  # pragma: no cover — Windows
                signal.signal(sig, lambda *_: stop_event.set())

        await watcher.start()

        file_watcher: ConfigFileWatcher | None = None
        if not no_reload:
            holder: ConfigHolder = ConfigHolder(initial=cfg)
            scheduler_sub = SchedulerSubscriber(
                scheduler=watcher.scheduler,
                domains_of=lambda c: _domains_from_config(c, watcher),
                callable_factory=watcher._make_job_callable,
            )
            holder.subscribe_object(scheduler_sub)
            file_watcher = ConfigFileWatcher(
                cfg_path,
                load_config,
                holder,
            )
            await file_watcher.start()

        try:
            await stop_event.wait()
        finally:
            if file_watcher is not None:
                await file_watcher.stop()
            await watcher.stop()

    with contextlib.suppress(KeyboardInterrupt):
        # SIGINT/SIGTERM are wired through ``stop_event`` above; this only
        # covers a stray Ctrl-C between asyncio.run() entry and signal-handler
        # registration.
        asyncio.run(_serve())
    raise typer.Exit(code=0)


def _domains_from_config(cfg: Config, watcher: object) -> Iterable[MonitoredDomain]:
    """Project a reloaded ``Config`` to the ``MonitoredDomain`` set.

    The cron + thresholds + channels live in the new config; the
    ``last_check`` field is preserved by the repository, not by the
    config holder.
    """
    from domain_watcher.composition import _build_domain  # local import; private helper

    defaults = tuple(cfg.notification_defaults.thresholds)
    return tuple(_build_domain(d, defaults=defaults) for d in cfg.domains)


_ = JobCallable  # keep import discoverable for typing reference

__all__ = ["run_command"]
