"""``DomainWatcherBuilder`` — fluent code-driven wiring (no YAML).

Use when an integrator wants explicit Python control over plugin
selection, thresholds, and the state backend. The bot repo (out of
scope here) builds its own façade with a custom ``ChannelResolver``
this way.

Defaults (kept conservative; override with the ``with_*`` setters):

- repo / idempotency: in-memory.
- scheduler: ``ApsScheduler`` (real APScheduler).
- clock: ``SystemClock``.
- retry: ``RetryPolicy()`` (3 attempts, 1s base, factor 5).
- thresholds: ``(30d, 7d, 1d)``.
- schedule: ``"0 */6 * * *"``.

``build()`` raises ``ValueError`` when at least one checker AND one
notifier have not been registered — running without either is always
a programming error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from domain_watcher.application.channel_resolver import StaticChannelResolver
from domain_watcher.application.event_bus import InProcessEventBus
from domain_watcher.application.scheduling import SchedulerService
from domain_watcher.core.checking.policies import RetryPolicy
from domain_watcher.core.checking.ports import ExpirationChecker
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.notification.policies import NotificationPolicy
from domain_watcher.core.notification.ports import (
    ChannelResolver,
    IdempotencyStore,
    Notifier,
)
from domain_watcher.core.shared.time_provider import SystemClock, TimeProvider
from domain_watcher.core.shared.value_objects import Duration
from domain_watcher.infrastructure.persistence.memory.idempotency import (
    MemoryIdempotencyStore,
)
from domain_watcher.infrastructure.persistence.memory.monitored import (
    MemoryMonitoredDomainRepo,
)
from domain_watcher.infrastructure.registry import Registry
from domain_watcher.infrastructure.scheduling.apscheduler import ApsScheduler

if TYPE_CHECKING:
    from domain_watcher.core.monitoring.ports import MonitoredDomainRepository
    from domain_watcher.interfaces.library.api import DomainWatcher


class DomainWatcherBuilder:
    """Fluent builder: incremental setters, single ``build()`` at the end."""

    def __init__(self) -> None:
        self._checkers: list[ExpirationChecker] = []
        self._notifiers: list[Notifier] = []
        self._channel_to_notifier: dict[str, str] = {}
        self._default_thresholds: tuple[Duration, ...] = (
            Duration.days(30),
            Duration.days(7),
            Duration.days(1),
        )
        self._default_schedule: str = "0 */6 * * *"
        self._retry_policy: RetryPolicy = RetryPolicy()
        self._clock: TimeProvider = SystemClock()
        self._repo: MonitoredDomainRepository | None = None
        self._idempotency: IdempotencyStore | None = None
        self._channel_resolver: ChannelResolver | None = None
        self._event_bus: InProcessEventBus | None = None
        self._scheduler: SchedulerService | None = None
        self._initial_domains: list[MonitoredDomain] = []

    # ------------------------------------------------------------------
    # Plugins
    # ------------------------------------------------------------------
    def with_checker(self, checker: ExpirationChecker) -> DomainWatcherBuilder:
        """Register an ``ExpirationChecker``. Duplicate ids raise at ``build()``."""
        self._checkers.append(checker)
        return self

    def with_notifier(
        self,
        notifier: Notifier,
        *,
        channel_id: str | None = None,
    ) -> DomainWatcherBuilder:
        """Register a ``Notifier``.

        ``channel_id`` (defaults to the notifier's own id) is what
        ``MonitoredDomain.channels`` references; the static resolver maps
        channel id → notifier id at dispatch time.
        """
        self._notifiers.append(notifier)
        cid = channel_id if channel_id is not None else notifier.id
        self._channel_to_notifier[cid] = notifier.id
        return self

    # ------------------------------------------------------------------
    # Defaults / policy
    # ------------------------------------------------------------------
    def with_default_thresholds(self, *thresholds: Duration) -> DomainWatcherBuilder:
        if not thresholds:
            raise ValueError("with_default_thresholds requires at least one Duration")
        self._default_thresholds = tuple(thresholds)
        return self

    def with_default_schedule(self, cron: str) -> DomainWatcherBuilder:
        self._default_schedule = cron
        return self

    def with_retry_policy(self, retry: RetryPolicy) -> DomainWatcherBuilder:
        self._retry_policy = retry
        return self

    def with_clock(self, clock: TimeProvider) -> DomainWatcherBuilder:
        self._clock = clock
        return self

    # ------------------------------------------------------------------
    # State backends / overrides
    # ------------------------------------------------------------------
    def with_repository(self, repo: MonitoredDomainRepository) -> DomainWatcherBuilder:
        self._repo = repo
        return self

    def with_idempotency_store(self, store: IdempotencyStore) -> DomainWatcherBuilder:
        self._idempotency = store
        return self

    def with_channel_resolver(self, resolver: ChannelResolver) -> DomainWatcherBuilder:
        self._channel_resolver = resolver
        return self

    def with_event_bus(self, bus: InProcessEventBus) -> DomainWatcherBuilder:
        self._event_bus = bus
        return self

    def with_scheduler(self, scheduler: SchedulerService) -> DomainWatcherBuilder:
        self._scheduler = scheduler
        return self

    def with_initial_domain(self, domain: MonitoredDomain) -> DomainWatcherBuilder:
        self._initial_domains.append(domain)
        return self

    def with_channel_routes(
        self,
        routes: Mapping[str, str],
    ) -> DomainWatcherBuilder:
        """Override the channel id → notifier id map.

        Example: ``{"team-a": "telegram"}``.
        """
        self._channel_to_notifier.update(routes)
        return self

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    def build(self) -> DomainWatcher:
        """Validate inputs and return a fully-wired ``DomainWatcher``."""
        if not self._checkers:
            raise ValueError(
                "DomainWatcherBuilder.build: at least one checker must be registered "
                "via with_checker(...)"
            )
        if not self._notifiers:
            raise ValueError(
                "DomainWatcherBuilder.build: at least one notifier must be registered "
                "via with_notifier(...)"
            )
        repo = self._repo or MemoryMonitoredDomainRepo()
        idempotency = self._idempotency or MemoryIdempotencyStore()
        bus = self._event_bus or InProcessEventBus()

        checker_registry = Registry[ExpirationChecker]()
        for c in self._checkers:
            checker_registry.register(c)
        notifier_registry = Registry[Notifier]()
        for n in self._notifiers:
            notifier_registry.register(n)

        resolver: ChannelResolver = (
            self._channel_resolver
            if self._channel_resolver is not None
            else StaticChannelResolver(dict(self._channel_to_notifier))
        )

        # Deferred import to break the api ↔ builder cycle.
        from domain_watcher.interfaces.library.api import DomainWatcher  # noqa: PLC0415

        scheduler: SchedulerService = (
            self._scheduler
            if self._scheduler is not None
            else _build_default_scheduler(repo)
        )

        watcher = DomainWatcher(
            repo=repo,
            idempotency=idempotency,
            checker_registry=checker_registry,
            notifier_registry=notifier_registry,
            channel_resolver=resolver,
            notification_policy=NotificationPolicy(thresholds=self._default_thresholds),
            retry_policy=self._retry_policy,
            scheduler=scheduler,
            clock=self._clock,
            event_bus=bus,
            default_thresholds=self._default_thresholds,
            default_schedule=self._default_schedule,
            initial_domains=tuple(self._initial_domains),
        )
        # Late wire: ApsScheduler can bootstrap from the repo using a
        # callable factory that defers to the watcher.
        _maybe_wire_scheduler_bootstrap(scheduler, watcher)
        return watcher


def _build_default_scheduler(repo: MonitoredDomainRepository) -> SchedulerService:
    """Build the default ``ApsScheduler`` with bootstrap_repo bound."""
    return ApsScheduler(bootstrap_repo=_BootstrapRepoAdapter(repo))


def _maybe_wire_scheduler_bootstrap(
    scheduler: SchedulerService,
    watcher: DomainWatcher,
) -> None:
    """If the scheduler is an ``ApsScheduler``, wire its bootstrap factory."""
    if isinstance(scheduler, ApsScheduler):
        # Re-create the scheduler with the bound factory in place.
        # The factory closes over the watcher so each MonitoredDomain
        # gets the correct callable.
        scheduler._bootstrap_callable_factory = watcher._make_job_callable


class _BootstrapRepoAdapter:
    """Narrow ``list_all`` adapter used by the scheduler at start time.

    The scheduler's bootstrap port wants only ``list_all``; the full repo
    interface is fine here but expressing the dependency narrowly makes
    the contract self-documenting.
    """

    __slots__ = ("_repo",)

    def __init__(self, repo: MonitoredDomainRepository) -> None:
        self._repo = repo

    async def list_all(self) -> Sequence[MonitoredDomain]:
        return await self._repo.list_all()


__all__ = ["DomainWatcherBuilder"]
