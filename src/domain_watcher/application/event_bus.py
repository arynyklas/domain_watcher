"""In-process event bus with two-tier delivery (ADR 0001 §11(2), ADR 0002 §6).

The bus implements the ``EventPublisher`` port from ``core/shared/events.py``
and exposes both a callback API (``on``/``on_any``) and an async-iterator API
(``events()``).

Two-tier queues per subscriber:

- **Critical queue** — unbounded. Used for events whose class declares
  ``criticality = "critical"`` (``DomainCheckFailed``, ``NotificationFailed``,
  ``ParseFailed``, ``WhoisRuleInvalidated``). ``publish`` waits up to
  ``critical_put_timeout`` per subscriber; on timeout it logs an ERROR and
  skips that single subscriber for that single event. Critical events are
  NEVER silently dropped — they may be missed by *one* wedged subscriber
  but the publisher continues with the rest.
- **Standard queue** — bounded. On overflow, the oldest standard event is
  dropped and a ``BusOverflow`` synthetic event is enqueued in its place.
  ``BusOverflow`` itself is standard so a wedged subscriber cannot loop.

Callback dispatch isolates handler exceptions: a raising handler MUST NOT
block other handlers. Iterators clean up automatically when garbage-
collected or cancelled.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, TypeVar, cast
from weakref import WeakSet

from domain_watcher.core.shared.events import Criticality, DomainEvent

if TYPE_CHECKING:
    from datetime import datetime

    pass

_log = logging.getLogger(__name__)

E = TypeVar("E", bound=DomainEvent)


@dataclass(frozen=True, slots=True)
class BusOverflow(DomainEvent):
    """Synthetic event published when a subscriber's standard queue overflowed.

    Standard criticality on purpose: a wedged subscriber that overflows must
    not receive a critical follow-up signaling its own overflow — that is the
    classic feedback-loop foot-gun.
    """

    subscriber_id: str = ""
    dropped_event_type: str = ""

    criticality: ClassVar[Criticality] = "standard"

    def __post_init__(self) -> None:
        if not self.subscriber_id:
            raise ValueError("BusOverflow.subscriber_id is required")
        if not self.dropped_event_type:
            raise ValueError("BusOverflow.dropped_event_type is required")


class _Subscriber:
    """One iterator-based subscriber with its own pair of queues."""

    __slots__ = ("_closed", "critical_q", "id", "standard_q")

    def __init__(self, sid: str, standard_maxsize: int) -> None:
        self.id = sid
        self.critical_q: asyncio.Queue[DomainEvent] = asyncio.Queue()
        self.standard_q: asyncio.Queue[DomainEvent] = asyncio.Queue(
            maxsize=standard_maxsize
        )
        self._closed = False

    def is_open(self) -> bool:
        return not self._closed

    def close(self) -> None:
        self._closed = True


class InProcessEventBus:
    """Thread-affine, asyncio-backed event bus.

    Constructed once per process. Use ``publish`` from anywhere; subscribe
    via ``on``, ``on_any``, or by consuming ``events()`` directly.
    """

    def __init__(
        self,
        *,
        standard_maxsize: int = 1024,
        critical_put_timeout: float = 5.0,
    ) -> None:
        self._standard_maxsize = standard_maxsize
        self._critical_put_timeout = critical_put_timeout
        # Iterator subscribers are tracked by strong reference; ``events()``
        # users call the helper which removes the subscriber when the
        # generator finalizes.
        self._subs: list[_Subscriber] = []
        self._typed_callbacks: dict[
            type[DomainEvent], list[Callable[[DomainEvent], Awaitable[None]]]
        ] = {}
        self._wildcard_callbacks: list[Callable[[DomainEvent], Awaitable[None]]] = []
        self._closed_subs: WeakSet[_Subscriber] = WeakSet()

    # -- subscription API --------------------------------------------------

    def on(
        self,
        event_type: type[E],
        handler: Callable[[E], Awaitable[None]],
    ) -> None:
        """Register an async callback for events of type ``event_type``.

        Subclasses match through ``isinstance`` semantics — a handler for
        ``DomainEvent`` receives every event.
        """
        bucket = self._typed_callbacks.setdefault(event_type, [])
        bucket.append(cast("Callable[[DomainEvent], Awaitable[None]]", handler))

    def on_any(self, handler: Callable[[DomainEvent], Awaitable[None]]) -> None:
        """Register an async callback for every event."""
        self._wildcard_callbacks.append(handler)

    def events(self) -> AsyncIterator[DomainEvent]:
        """Return an async iterator yielding every published event."""
        sub = _Subscriber(
            sid=f"iter-{len(self._subs)}",
            standard_maxsize=self._standard_maxsize,
        )
        self._subs.append(sub)
        return _SubscriberIterator(self, sub)

    # -- publishing --------------------------------------------------------

    async def publish(self, event: DomainEvent) -> None:
        """Fan out ``event`` to every subscriber and callback.

        Handler exceptions are caught and logged; one bad handler does not
        block siblings. Critical events get a per-subscriber timeout and an
        ERROR log if a subscriber's queue is wedged.
        """
        criticality: Criticality = type(event).criticality
        # Iterator subscribers
        for sub in list(self._subs):
            if not sub.is_open():
                continue
            await self._enqueue(sub, event, criticality)
        # Callbacks (typed + wildcard)
        await self._run_callbacks(event)

    async def _enqueue(
        self,
        sub: _Subscriber,
        event: DomainEvent,
        criticality: Criticality,
    ) -> None:
        if criticality == "critical":
            try:
                await asyncio.wait_for(
                    sub.critical_q.put(event), timeout=self._critical_put_timeout
                )
            except TimeoutError:
                _log.error(
                    "event_bus: critical event dropped for subscriber",
                    extra={
                        "subscriber_id": sub.id,
                        "event_type": type(event).__name__,
                        "timeout_s": self._critical_put_timeout,
                    },
                )
            return

        # Standard: drop-oldest with BusOverflow synthetic event.
        try:
            sub.standard_q.put_nowait(event)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                _ = sub.standard_q.get_nowait()
            sub.standard_q.put_nowait(event)
            overflow = BusOverflow(
                occurred_at=event.occurred_at,
                subscriber_id=sub.id,
                dropped_event_type=type(event).__name__,
            )
            try:
                sub.standard_q.put_nowait(overflow)
            except asyncio.QueueFull:
                # Drop the oldest again to make room — a wedged subscriber
                # at this point gets eventually drained or dies; the
                # synthetic must land if we keep room for it.
                with contextlib.suppress(asyncio.QueueEmpty):
                    _ = sub.standard_q.get_nowait()
                sub.standard_q.put_nowait(overflow)

    async def _run_callbacks(self, event: DomainEvent) -> None:
        # Typed handlers — match by exact type and superclasses we registered
        # against.
        for registered_type, handlers in self._typed_callbacks.items():
            if not isinstance(event, registered_type):
                continue
            for handler in handlers:
                await self._safe_call(handler, event)
        for handler in self._wildcard_callbacks:
            await self._safe_call(handler, event)

    async def _safe_call(
        self,
        handler: Callable[[DomainEvent], Awaitable[None]],
        event: DomainEvent,
    ) -> None:
        try:
            await handler(event)
        except Exception:
            _log.exception(
                "event_bus: handler raised",
                extra={"event_type": type(event).__name__, "handler": repr(handler)},
            )

    # -- iterator support --------------------------------------------------

    def _drop_subscriber(self, sub: _Subscriber) -> None:
        sub.close()
        with contextlib.suppress(ValueError):
            self._subs.remove(sub)


class _SubscriberIterator(AsyncIterator[DomainEvent]):
    """Drains both queues in a critical-first order until cancelled."""

    __slots__ = ("_bus", "_sub")

    def __init__(self, bus: InProcessEventBus, sub: _Subscriber) -> None:
        self._bus = bus
        self._sub = sub

    def __aiter__(self) -> AsyncIterator[DomainEvent]:
        return self

    async def __anext__(self) -> DomainEvent:
        if not self._sub.is_open():
            raise StopAsyncIteration
        try:
            return await self._wait_for_either()
        except asyncio.CancelledError:
            self._bus._drop_subscriber(self._sub)
            raise

    async def _wait_for_either(self) -> DomainEvent:
        crit = asyncio.create_task(self._sub.critical_q.get())
        std = asyncio.create_task(self._sub.standard_q.get())
        try:
            done, _pending = await asyncio.wait(
                {crit, std}, return_when=asyncio.FIRST_COMPLETED
            )
            # Prefer critical when both completed simultaneously.
            if crit in done:
                # Re-queue any standard event we already pulled to preserve
                # ordering across calls.
                if std in done:
                    self._sub.standard_q.put_nowait(std.result())
                return crit.result()
            return std.result()
        finally:
            for task in (crit, std):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, BaseException):
                        await task

    def __del__(self) -> None:  # pragma: no cover — cleanup hook
        with contextlib.suppress(Exception):
            self._bus._drop_subscriber(self._sub)


def utc_now_naive_unsupported() -> datetime:  # pragma: no cover — placeholder
    """Reserved hook so importers do not accidentally call ``datetime.utcnow``.

    Defined here to keep a clear failure if a refactor introduces it: this
    name has no in-tree callers and is exported only by intent.
    """
    raise RuntimeError("never call utcnow; inject TimeProvider")


__all__ = ["BusOverflow", "InProcessEventBus"]
