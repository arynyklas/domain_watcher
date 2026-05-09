"""Hot-reload watcher: file change → load → validate → ``ConfigHolder.update``.

Wraps ``watchdog.observers.Observer`` (a thread-based file-system poller)
behind an asyncio bridge. Editor-save patterns (atomic replace, move,
truncate) are normalised: we watch the **directory** containing the file
and filter events by basename, so file deletions or replacements do not
silently stop the watcher.

A failed reload **never** propagates: validation/IO errors are logged and
the previous good config remains the source of truth. This is the
ADR 0003 §6 contract.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, TypeVar

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from domain_watcher.core.shared.errors import ConfigError

if TYPE_CHECKING:
    from pathlib import Path

    from watchdog.events import FileSystemEvent
    from watchdog.observers.api import BaseObserver


_log = logging.getLogger(__name__)

_DEFAULT_DEBOUNCE_SECONDS = 0.200


_C = TypeVar("_C")

ConfigLoader = Callable[["Path"], "_C"]
"""Sync callable that reads a path and returns a validated config of type ``_C``.

Defaults to ``loader.load_config`` in production; tests inject stubs.
"""


class ConfigSink(Protocol[_C]):
    """Minimal contract the watcher needs from a config holder.

    Defined locally so the ``infrastructure`` layer does not import
    ``application.ConfigHolder`` (would violate the layered-architecture
    contract). ``ConfigHolder`` satisfies this Protocol structurally.
    """

    async def update(self, new: _C) -> None: ...


class ConfigFileWatcher[C]:
    """Watch ``path`` and apply each successful reload to ``holder``."""

    __slots__ = (
        "_debounce_seconds",
        "_handler",
        "_holder",
        "_loader",
        "_loop",
        "_observer",
        "_path",
        "_pending",
        "_reload_lock",
    )

    def __init__(
        self,
        path: Path,
        loader: Callable[[Path], C],
        holder: ConfigSink[C],
        *,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self._path = path.resolve()
        self._loader = loader
        self._holder = holder
        self._debounce_seconds = debounce_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observer: BaseObserver | None = None
        self._handler: _PathHandler | None = None
        self._pending: asyncio.TimerHandle | None = None
        self._reload_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Bind to the running event loop and start the underlying observer."""
        if self._observer is not None:
            return
        self._loop = asyncio.get_running_loop()
        handler = _PathHandler(self._path.name, self._on_change)
        observer = Observer()
        observer.schedule(handler, str(self._path.parent), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer
        self._handler = handler

    async def stop(self) -> None:
        """Stop the observer and cancel any pending debounced reload."""
        observer, self._observer = self._observer, None
        if observer is not None:
            observer.stop()
            # ``observer.join`` blocks; run in executor so we don't block the loop.
            await asyncio.to_thread(observer.join, 5.0)
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None

    # ------------------------------------------------------------------
    # Bridge: watchdog thread → asyncio loop
    # ------------------------------------------------------------------
    def _on_change(self) -> None:
        """Called from the watchdog thread when our path changes."""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._schedule_reload)

    # ------------------------------------------------------------------
    # asyncio-side: debounce + reload
    # ------------------------------------------------------------------
    def _schedule_reload(self) -> None:
        """(asyncio thread) Cancel any in-flight debounce and re-arm."""
        if self._pending is not None:
            self._pending.cancel()
        loop = self._loop
        assert loop is not None
        self._pending = loop.call_later(
            self._debounce_seconds,
            lambda: loop.create_task(self._do_reload()),
        )

    async def _do_reload(self) -> None:
        """(asyncio thread) Re-read the file and update the holder."""
        self._pending = None
        async with self._reload_lock:
            try:
                # Loader is sync; off-load to thread to keep the loop responsive.
                new_cfg = await asyncio.to_thread(self._loader, self._path)
            except ConfigError as exc:
                _log.error("config reload failed; keeping previous config: %s", exc)
                return
            except Exception:
                # Unexpected error: log with stack but never crash the daemon.
                _log.exception("config reload raised unexpectedly; keeping previous config")
                return
            try:
                await self._holder.update(new_cfg)
            except Exception:
                # Subscriber-isolated by ConfigHolder; this branch should be rare.
                _log.exception("config holder.update raised; previous config still active")

    # ------------------------------------------------------------------
    # Test seam
    # ------------------------------------------------------------------
    async def trigger_reload_for_tests(self) -> None:
        """Force an immediate (non-debounced) reload — for unit tests only."""
        await self._do_reload()


class _PathHandler(FileSystemEventHandler):
    """Filter directory events down to changes touching our specific file."""

    __slots__ = ("_basename", "_callback")

    def __init__(self, basename: str, callback: Callable[[], None]) -> None:
        self._basename = basename
        self._callback = callback

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # ``src_path`` covers create/modify/delete; ``dest_path`` covers move-into.
        for raw in (
            event.src_path,
            getattr(event, "dest_path", "") or "",
        ):
            if not raw:
                continue
            path = raw if isinstance(raw, str) else raw.decode("utf-8", "ignore")
            # path could be absolute, relative, or just the basename. Normalise
            # by taking the trailing component and matching exact basename.
            if path.split("/")[-1] == self._basename:
                self._callback()
                return


__all__ = [
    "ConfigFileWatcher",
    "ConfigLoader",
]
