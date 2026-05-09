"""``ConfigHolder`` — atomic config swap with subscriber fan-out.

The Pydantic schema and YAML loader arrive in Phase 7; for now this module
defines:

- ``ConfigSubscriber``: Protocol with ``on_config_changed(old, new)``.
- ``ConfigHolder``: thread-safe wrapper that holds the current ``Config``
  and broadcasts changes to subscribers, isolating subscriber exceptions.

``Config`` is a ``TypeVar`` so callers can plug in any frozen dataclass /
Pydantic model. Phase 7 will pin the concrete type.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar, runtime_checkable

_log = logging.getLogger(__name__)

C = TypeVar("C")


@runtime_checkable
class ConfigSubscriber(Protocol[C]):
    """Notified after the holder swaps to a new config."""

    async def on_config_changed(self, old: C | None, new: C) -> None: ...


SubscriberCallable = Callable[[C | None, C], Awaitable[None]]


class ConfigHolder[C]:
    """Atomic swap + subscriber fan-out.

    ``update(new)`` first replaces the in-memory reference, then notifies
    every subscriber concurrently. Subscriber exceptions are logged and
    swallowed: one bad subscriber MUST NOT block another (ADR 0003 §5).
    """

    __slots__ = ("_current", "_lock", "_subscribers")

    def __init__(self, initial: C | None = None) -> None:
        self._current: C | None = initial
        self._subscribers: list[SubscriberCallable[C]] = []
        self._lock = asyncio.Lock()

    @property
    def current(self) -> C | None:
        return self._current

    def subscribe(self, fn: SubscriberCallable[C]) -> None:
        self._subscribers.append(fn)

    def subscribe_object(self, sub: ConfigSubscriber[C]) -> None:
        self._subscribers.append(sub.on_config_changed)

    async def update(self, new: C) -> None:
        async with self._lock:
            old = self._current
            self._current = new
            await self._fan_out(old, new)

    async def _fan_out(self, old: C | None, new: C) -> None:
        if not self._subscribers:
            return

        async def _safe(fn: SubscriberCallable[C]) -> None:
            try:
                await fn(old, new)
            except Exception:
                _log.exception("config holder: subscriber raised", extra={"sub": repr(fn)})

        await asyncio.gather(*[_safe(s) for s in self._subscribers], return_exceptions=False)


__all__ = ["ConfigHolder", "ConfigSubscriber", "SubscriberCallable"]
