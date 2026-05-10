"""Public ``DomainWatcher`` façade — embedded library entry point.

This is what third-party integrators import:

    from domain_watcher import DomainWatcher

The façade owns:

- a ``MonitoredDomainRepository`` (the persistent set of watched domains),
- a ``Registry[ExpirationChecker]`` and ``Registry[Notifier]`` populated
  by the builder or composition root,
- a ``ChannelResolver`` mapping channel ids to notifier ids,
- a ``SchedulerService`` (default: ``ApsScheduler``) that drives recurring
  checks,
- an ``InProcessEventBus`` exposing the same events the CLI surfaces,
- a ``NotificationPolicy`` + ``IdempotencyStore`` + ``RetryPolicy`` so
  ``check_now`` reproduces the same dispatch path the daemon uses.

Lifecycle:

- ``start()`` populates the repository from ``initial_domains`` (only on
  first start) and starts the scheduler, which bootstraps from
  ``repo.list_all()`` BEFORE its first tick.
- ``stop()`` is idempotent and shuts the scheduler down cleanly.
- ``check_now`` runs the same orchestration the scheduler triggers,
  returning the ``CheckResult`` to the caller.

The bot repo (out of scope here, ADR 0005) embeds this façade and
supplies its own ``ChannelResolver`` to fan an alert out to subscribers.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

from domain_watcher.application.event_bus import InProcessEventBus
from domain_watcher.application.scheduling import JobCallable, SchedulerService
from domain_watcher.application.use_cases.check_domain import (
    CheckDomainUseCase,
    DomainNotMonitoredError,
)
from domain_watcher.application.use_cases.dispatch_notifications import (
    DispatchNotificationsUseCase,
)
from domain_watcher.core.checking.policies import RetryPolicy
from domain_watcher.core.checking.ports import ExpirationChecker
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import (
    ChannelId,
    CheckSchedule,
)
from domain_watcher.core.notification.policies import NotificationPolicy
from domain_watcher.core.notification.ports import (
    ChannelResolver,
    IdempotencyStore,
    Notifier,
)
from domain_watcher.core.shared.events import DomainEvent
from domain_watcher.core.shared.time_provider import SystemClock, TimeProvider
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.registry import Registry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from domain_watcher.core.checking.value_objects import CheckResult
    from domain_watcher.core.monitoring.ports import MonitoredDomainRepository

_log = logging.getLogger(__name__)

E = TypeVar("E", bound=DomainEvent)


@dataclass(slots=True)
class DomainWatcher:
    """Embeddable orchestrator. Built by ``DomainWatcherBuilder`` or composition."""

    repo: MonitoredDomainRepository
    idempotency: IdempotencyStore
    checker_registry: Registry[ExpirationChecker]
    notifier_registry: Registry[Notifier]
    channel_resolver: ChannelResolver
    notification_policy: NotificationPolicy
    retry_policy: RetryPolicy
    scheduler: SchedulerService
    clock: TimeProvider = field(default_factory=SystemClock)
    event_bus: InProcessEventBus = field(default_factory=InProcessEventBus)
    default_thresholds: tuple[Duration, ...] = (
        Duration.days(30),
        Duration.days(7),
        Duration.days(1),
    )
    default_schedule: str = "0 */6 * * *"
    initial_domains: tuple[MonitoredDomain, ...] = ()
    aclose_hooks: tuple[Callable[[], Awaitable[None]], ...] = ()
    start_hooks: tuple[Callable[[], Awaitable[None]], ...] = ()
    learned_rules_repo: object | None = None
    """Optional reference to the ``LearnedRulesRepository`` used by the
    rules CLI. Composition wires this; the builder leaves it ``None`` if
    no learned-rule store was configured."""
    _started: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------
    @classmethod
    def from_config_file(cls, path: str | Path) -> DomainWatcher:
        """Load YAML at ``path`` and return a fully-wired instance.

        Always returns ``DomainWatcher`` (not ``Self``): composition has
        an opinion about the concrete type — subclassing the façade is
        not a supported extension point. Override ``builder()`` and
        instantiate manually if you need a subclass.
        """
        from domain_watcher.composition import compose_from_config
        from domain_watcher.infrastructure.config.loader import load_config

        cfg = load_config(path)
        return compose_from_config(cfg)

    @classmethod
    def builder(cls) -> DomainWatcherBuilder:
        """Return a fresh builder for code-driven wiring (no YAML)."""
        return DomainWatcherBuilder()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Seed the repository from ``initial_domains`` and start the scheduler.

        Idempotent: subsequent calls are no-ops. ``initial_domains`` are
        upserted (existing rows keep their ``last_check``); the scheduler
        bootstraps from ``repo.list_all()`` before its first tick so an
        embedded caller never observes an empty job set.
        """
        if self._started:
            return
        for d in self.initial_domains:
            existing = await self.repo.get(d.name)
            if existing is None:
                await self.repo.add(d)
            else:
                merged = MonitoredDomain(
                    name=d.name,
                    schedule=d.schedule,
                    checker_id=d.checker_id,
                    notify_thresholds=d.notify_thresholds,
                    channels=d.channels,
                    last_check=existing.last_check,
                    metadata=dict(d.metadata),
                )
                await self.repo.update(merged)
        await self.scheduler.start()
        for hook in self.start_hooks:
            try:
                await hook()
            except Exception:
                _log.exception("start hook raised")
        self._started = True

    async def stop(self) -> None:
        """Shut the scheduler down cleanly. Idempotent."""
        if not self._started:
            return
        try:
            await self.scheduler.stop()
        finally:
            self._started = False
            for hook in self.aclose_hooks:
                try:
                    await hook()
                except Exception:
                    _log.exception("aclose hook raised during stop")

    # ------------------------------------------------------------------
    # Watch-set management
    # ------------------------------------------------------------------
    async def ensure_watching(
        self,
        domain: DomainName,
        *,
        checker_id: str,
        schedule: str | None = None,
        channels: Sequence[ChannelId | str],
        thresholds: Sequence[Duration] | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Idempotent upsert: repo + scheduler add-or-update.

        ``last_check`` is preserved across calls. Same args twice does NOT
        duplicate the scheduler job (``add_or_update_job`` with identical
        args is a true no-op).
        """
        if checker_id not in self.checker_registry:
            raise KeyError(
                f"DomainWatcher.ensure_watching: unknown checker id {checker_id!r}; "
                f"registered: {sorted(c.id for c in self.checker_registry.all())}"
            )
        cron = schedule if schedule is not None else self.default_schedule
        chan_ids = tuple(c if isinstance(c, ChannelId) else ChannelId(c) for c in channels)
        if not chan_ids:
            raise ValueError("ensure_watching: channels cannot be empty")
        thresholds_t = tuple(thresholds) if thresholds is not None else self.default_thresholds
        existing = await self.repo.get(domain)
        new = MonitoredDomain(
            name=domain,
            schedule=CheckSchedule(cron),
            checker_id=checker_id,
            notify_thresholds=thresholds_t,
            channels=chan_ids,
            last_check=existing.last_check if existing is not None else None,
            metadata=dict(metadata or {}),
        )
        if existing is None:
            await self.repo.add(new)
        else:
            await self.repo.update(new)
        await self.scheduler.add_or_update_job(domain, cron, self._make_job_callable(new))

    async def remove_watching(self, domain: DomainName) -> None:
        """Cancel the scheduler job and remove the repo row.

        Alert idempotency rows for ``domain`` are intentionally NOT
        deleted: re-adding the same domain in the same expiration cycle
        must not re-fire alerts the operator already saw.
        """
        await self.scheduler.remove_job(domain)
        await self.repo.remove(domain)

    # ------------------------------------------------------------------
    # Direct check
    # ------------------------------------------------------------------
    async def check_now(self, domain: DomainName) -> CheckResult:
        """Run a single check + dispatch synchronously and return the result."""
        existing = await self.repo.get(domain)
        if existing is None:
            raise DomainNotMonitoredError(f"no monitored domain {domain.value!r}")
        previous_last_check = existing.last_check
        check_uc = self._make_check_use_case()
        result = await check_uc.execute(domain)
        # Re-read so dispatch uses the post-update aggregate (with current channels).
        refreshed = await self.repo.get(domain)
        if refreshed is not None:
            dispatch_uc = self._make_dispatch_use_case(refreshed.notify_thresholds)
            await dispatch_uc.execute(refreshed, previous_last_check, result)
        return result

    # ------------------------------------------------------------------
    # Event subscription (delegates to the bus)
    # ------------------------------------------------------------------
    def events(self) -> AsyncIterator[DomainEvent]:
        return self.event_bus.events()

    def on(
        self,
        event_type: type[E],
        handler: Callable[[E], Awaitable[None]],
    ) -> None:
        self.event_bus.on(event_type, handler)

    def on_any(self, handler: Callable[[DomainEvent], Awaitable[None]]) -> None:
        self.event_bus.on_any(handler)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _checker_dict(self) -> dict[str, ExpirationChecker]:
        return {c.id: c for c in self.checker_registry.all()}

    def _notifier_dict(self) -> dict[str, Notifier]:
        return {n.id: n for n in self.notifier_registry.all()}

    def _make_check_use_case(self) -> CheckDomainUseCase:
        return CheckDomainUseCase(
            repo=self.repo,
            checkers=self._checker_dict(),
            retry_policy=self.retry_policy,
            publisher=self.event_bus,
            clock=self.clock,
        )

    def _make_dispatch_use_case(
        self,
        thresholds: tuple[Duration, ...],
    ) -> DispatchNotificationsUseCase:
        # Domain-specific thresholds override the policy default.
        policy = (
            self.notification_policy
            if thresholds == self.notification_policy.thresholds
            else NotificationPolicy(thresholds=thresholds)
        )
        return DispatchNotificationsUseCase(
            policy=policy,
            resolver=self.channel_resolver,
            idempotency=self.idempotency,
            notifiers=self._notifier_dict(),
            publisher=self.event_bus,
            clock=self.clock,
            retry_policy=self.retry_policy,
        )

    def _make_job_callable(self, _domain: MonitoredDomain) -> JobCallable:
        name = _domain.name

        async def _run() -> None:
            try:
                await self.check_now(name)
            except DomainNotMonitoredError:
                # Domain was removed between scheduling and firing — drop quietly.
                _log.info("scheduled check skipped: %s no longer monitored", name.value)
            except Exception:
                _log.exception("scheduled check failed for %s", name.value)

        return _run


__all__ = ["DomainWatcher"]


# Late import to avoid the api ↔ builder cycle: ``builder.py`` imports
# ``DomainWatcher`` from this module via a deferred import inside its
# ``build()`` method. Importing ``DomainWatcherBuilder`` here at the
# bottom of the module — after ``DomainWatcher`` is fully defined — lets
# ``DomainWatcher.builder()`` create an instance without a runtime cycle.
from domain_watcher.interfaces.library.builder import (  # noqa: E402
    DomainWatcherBuilder as DomainWatcherBuilder,
)
