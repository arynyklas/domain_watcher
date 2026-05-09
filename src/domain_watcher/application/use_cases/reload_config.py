"""``ConfigHolder`` and reconciliation subscribers (ADR 0003 §5).

The holder owns the current ``Config`` reference and broadcasts changes to
subscribers, isolating subscriber exceptions. Three first-party
subscribers translate config diffs into operational reconciliation:

- :class:`SchedulerSubscriber` — diff domain set, call
  :meth:`SchedulerService.reconcile`.
- :class:`RegistrySubscriber` — diff plugin spec set, register / unregister
  / replace (or hot-reload via ``HotReloadable.reload``) per id.
- :class:`ParsingSubscriber` — atomic swap of WHOIS rules.

Subscribers operate via injected callbacks rather than concrete
infrastructure types so the ``application`` layer never imports
``infrastructure``. The composition root binds these callbacks to the
real ``Registry``, ``SchedulerService``, and rule-holder mechanisms.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from domain_watcher.application.scheduling import JobCallable, SchedulerService
    from domain_watcher.core.monitoring.entities import MonitoredDomain
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


# ---------------------------------------------------------------------------
# Reconciliation subscribers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PluginSpec:
    """Per-plugin reconciliation input.

    Subscribers consume tuples of these to diff old vs new config without
    knowing the concrete ``CheckerConfig`` / ``NotifierConfig`` types.
    """

    id: str
    type: str
    settings: Mapping[str, object] = field(default_factory=dict)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PluginSpec):
            return NotImplemented
        return (
            self.id == other.id
            and self.type == other.type
            and dict(self.settings) == dict(other.settings)
        )

    def __hash__(self) -> int:
        return hash((self.id, self.type, tuple(sorted(self.settings.items()))))


@runtime_checkable
class HotReloadable(Protocol):
    """Plugin instance supporting in-place settings reload.

    Returning ``True`` tells the registry subscriber the plugin absorbed
    the new settings and re-instantiation can be skipped. Returning
    ``False`` falls through to the standard replace path.
    """

    async def reload(self, settings: Mapping[str, object]) -> bool: ...


_MaybeAwaitable = Awaitable[None] | None


@dataclass(frozen=True, slots=True)
class SchedulerSubscriber[C]:
    """Reconcile scheduler jobs from the new config's domain set.

    ``domains_of`` projects the config to its monitored-domain sequence;
    ``callable_factory`` builds the per-domain check coroutine.
    """

    scheduler: SchedulerService
    domains_of: Callable[[C], Iterable[MonitoredDomain]]
    callable_factory: Callable[[MonitoredDomain], JobCallable]

    async def on_config_changed(self, old: C | None, new: C) -> None:
        domains = list(self.domains_of(new))
        await self.scheduler.reconcile(domains, callable_factory=self.callable_factory)


@dataclass(frozen=True, slots=True)
class RegistrySubscriber[C, T]:
    """Diff plugin specs and apply add / remove / replace / hot-reload.

    Behaviours:
      - new id (in ``new`` but not ``old``) → ``register(factory(spec))``
      - removed id (in ``old`` but not ``new``) → ``unregister(id)``;
        in-flight callers retain their bound reference, new lookups raise
        ``PluginNotFoundError``.
      - id in both with identical ``(type, settings)`` → no-op.
      - id in both with different ``type`` → ``replace(id, factory(spec))``.
      - id in both with same ``type`` but different ``settings`` → if a
        ``reload_hook`` is provided and the current instance's
        ``HotReloadable.reload`` returns ``True``, the instance is kept;
        otherwise the path falls through to ``replace``.
    """

    plugins_of: Callable[[C], Iterable[PluginSpec]]
    instance_of: Callable[[str], T | None]
    factory: Callable[[PluginSpec], T]
    register: Callable[[T], _MaybeAwaitable]
    unregister: Callable[[str], _MaybeAwaitable]
    replace: Callable[[str, T], _MaybeAwaitable]
    reload_hook: Callable[[T, Mapping[str, object]], Awaitable[bool]] | None = None

    async def on_config_changed(self, old: C | None, new: C) -> None:
        old_specs: dict[str, PluginSpec] = (
            {p.id: p for p in self.plugins_of(old)} if old is not None else {}
        )
        new_specs: dict[str, PluginSpec] = {p.id: p for p in self.plugins_of(new)}

        for removed_id in sorted(old_specs.keys() - new_specs.keys()):
            await _maybe_await(self.unregister(removed_id))

        for added_id in sorted(new_specs.keys() - old_specs.keys()):
            inst = self.factory(new_specs[added_id])
            await _maybe_await(self.register(inst))

        for plugin_id in sorted(new_specs.keys() & old_specs.keys()):
            old_spec = old_specs[plugin_id]
            new_spec = new_specs[plugin_id]
            if old_spec == new_spec:
                continue
            if old_spec.type != new_spec.type:
                await _maybe_await(self.replace(plugin_id, self.factory(new_spec)))
                continue
            if self.reload_hook is not None:
                current = self.instance_of(plugin_id)
                if current is not None:
                    try:
                        kept = await self.reload_hook(current, new_spec.settings)
                    except Exception:
                        _log.exception(
                            "reload_hook raised for %r; falling back to replace", plugin_id
                        )
                        kept = False
                    if kept:
                        continue
            await _maybe_await(self.replace(plugin_id, self.factory(new_spec)))


@dataclass(frozen=True, slots=True)
class ParsingSubscriber[C, R]:
    """Atomic swap of WHOIS parse rules from the new config.

    ``rules_of`` projects ``C → Sequence[R]`` (typically ``ParseRule``);
    ``apply`` installs the new rule set in the application's parsing
    holder.
    """

    rules_of: Callable[[C], Sequence[R]]
    apply: Callable[[Sequence[R]], Awaitable[None]]

    async def on_config_changed(self, old: C | None, new: C) -> None:
        await self.apply(self.rules_of(new))


async def _maybe_await(value: _MaybeAwaitable) -> None:
    if value is None:
        return
    await value


__all__ = [
    "ConfigHolder",
    "ConfigSubscriber",
    "HotReloadable",
    "ParsingSubscriber",
    "PluginSpec",
    "RegistrySubscriber",
    "SchedulerSubscriber",
    "SubscriberCallable",
]
