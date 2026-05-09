"""Composition root: wire a ``Config`` into a fully-built ``DomainWatcher``.

The single place that imports both core ports and infrastructure
adapters. Returns the same ``DomainWatcher`` façade an integrator could
have built by hand via ``DomainWatcherBuilder``; YAML is just one path
in.

Driver factories (``_CHECKER_FACTORIES``, ``_NOTIFIER_FACTORIES``) are
type → callable maps. A type referenced in YAML that isn't in the map is
a ``ConfigError`` at compose time — catching plugin name typos at the
config boundary instead of letting them crash inside ``check_now``.

State backend selection (``runtime.state_db``):

- ``sqlite:///PATH`` or ``sqlite+aiosqlite:///PATH`` → SQLAlchemy + aiosqlite
- ``postgresql+asyncpg://...``                       → SQLAlchemy + asyncpg
- ``memory://``                                      → MemoryMonitoredDomainRepo etc.

When SQL is selected, the composition opens an ``async_sessionmaker``
and instantiates the SQL repos under a single session per call.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import httpx
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from domain_watcher.application.channel_resolver import StaticChannelResolver
from domain_watcher.application.event_bus import InProcessEventBus
from domain_watcher.application.services.parsing_service import ParsingService
from domain_watcher.core.checking.policies import RetryPolicy
from domain_watcher.core.checking.ports import ExpirationChecker
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import (
    ChannelId,
    CheckSchedule,
)
from domain_watcher.core.notification.policies import NotificationPolicy
from domain_watcher.core.notification.ports import Notifier
from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.errors import ConfigError
from domain_watcher.core.shared.time_provider import SystemClock, TimeProvider
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.checkers._iana_bootstrap import IanaBootstrap
from domain_watcher.infrastructure.checkers._whois_with_parser import (
    WhoisCheckerWithParser,
)
from domain_watcher.infrastructure.checkers.rdap import RdapChecker
from domain_watcher.infrastructure.checkers.script import ScriptChecker
from domain_watcher.infrastructure.checkers.whois import _WhoisFetcher
from domain_watcher.infrastructure.notifiers.discord import DiscordNotifier
from domain_watcher.infrastructure.notifiers.email_smtp import EmailNotifier
from domain_watcher.infrastructure.notifiers.telegram import TelegramNotifier
from domain_watcher.infrastructure.notifiers.webhook import WebhookNotifier
from domain_watcher.infrastructure.parsers.llm_suggester import LiteLLMRuleSuggester
from domain_watcher.infrastructure.parsers.regex import RegexWhoisParser
from domain_watcher.infrastructure.parsers.safety import (
    PerTldLimiter,
    SuggesterCircuitBreaker,
    TokenBucketLimiter,
    default_circuit_breaker,
)
from domain_watcher.infrastructure.parsers.validation_pipeline import (
    ValidationPipeline,
)
from domain_watcher.infrastructure.persistence.memory.idempotency import (
    MemoryIdempotencyStore,
)
from domain_watcher.infrastructure.persistence.memory.learned_rules import (
    MemoryLearnedRulesRepo,
)
from domain_watcher.infrastructure.persistence.memory.monitored import (
    MemoryMonitoredDomainRepo,
)
from domain_watcher.infrastructure.persistence.sql.repos.idempotency import (
    SqlIdempotencyStore,
)
from domain_watcher.infrastructure.persistence.sql.repos.learned_rules import (
    SqlLearnedRulesRepo,
)
from domain_watcher.infrastructure.persistence.sql.repos.monitored import (
    SqlMonitoredDomainRepo,
)
from domain_watcher.infrastructure.registry import Registry
from domain_watcher.infrastructure.scheduling.apscheduler import ApsScheduler
from domain_watcher.interfaces.library.api import DomainWatcher

if TYPE_CHECKING:
    from domain_watcher.core.monitoring.ports import MonitoredDomainRepository
    from domain_watcher.core.notification.ports import IdempotencyStore
    from domain_watcher.core.parsing.ports import LearnedRulesRepository
    from domain_watcher.infrastructure.config.schema import (
        CheckerConfig,
        Config,
        DomainEntry,
        NotifierConfig,
        WhoisRule,
    )

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ComposedRepos:
    domain_repo: Any
    idempotency: IdempotencyStore
    learned_rules: LearnedRulesRepository
    aclose: Callable[[], Awaitable[None]] | None


def compose_from_config(
    config: Config,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> DomainWatcher:
    """Wire a fully-validated ``Config`` into a ``DomainWatcher``.

    The optional ``http_client`` overrides the default ``httpx.AsyncClient``
    used by HTTP-backed plugins (RDAP, Discord, Telegram, webhook). Tests
    inject one with a ``MockTransport``; production passes ``None`` and
    each plugin owns its own client.
    """
    clock: TimeProvider = SystemClock()
    bus = InProcessEventBus()

    repos = _build_repos(config.runtime.state_db)

    # Parsing service (built first so checkers can pin its parse method)
    parsing_service, parsing_aclose = _build_parsing_service(
        config=config,
        clock=clock,
        bus=bus,
        learned_rules=repos.learned_rules,
        http_client=http_client,
    )

    # Checker registry
    checker_registry = Registry[ExpirationChecker]()
    aclose_hooks: list[Callable[[], Awaitable[None]]] = []
    for cc in config.checkers:
        instance, instance_aclose = _build_checker(
            cc,
            parsing_service=parsing_service,
            http_client=http_client,
        )
        checker_registry.register(instance)
        if instance_aclose is not None:
            aclose_hooks.append(instance_aclose)

    # Notifier registry
    notifier_registry = Registry[Notifier]()
    channel_to_notifier: dict[str, str] = {}
    for nc in config.notifiers:
        instance, instance_aclose = _build_notifier(nc, http_client=http_client)
        notifier_registry.register(instance)
        # Per ADR 0003, the notifier id IS the channel id at the config layer.
        channel_to_notifier[nc.id] = nc.id
        if instance_aclose is not None:
            aclose_hooks.append(instance_aclose)

    if parsing_aclose is not None:
        aclose_hooks.append(parsing_aclose)
    if repos.aclose is not None:
        aclose_hooks.append(repos.aclose)

    # Notification policy + retry
    policy = NotificationPolicy(thresholds=tuple(config.notification_defaults.thresholds))
    retry_policy = RetryPolicy(
        max_attempts=config.notification_defaults.retry.max_attempts,
        base_delay=config.notification_defaults.retry.base_delay,
        factor=config.notification_defaults.retry.factor,
    )

    _defaults = tuple(config.notification_defaults.thresholds)
    initial_domains = tuple(_build_domain(d, defaults=_defaults) for d in config.domains)

    # Validate domain references against registries
    known_checker_ids = {c.id for c in checker_registry.all()}
    for d in initial_domains:
        if d.checker_id not in known_checker_ids:
            raise ConfigError(
                f"composition: domain {d.name.value!r} references unknown checker "
                f"{d.checker_id!r}; known: {sorted(known_checker_ids)}"
            )
        for ch in d.channels:
            if ch.value not in channel_to_notifier:
                raise ConfigError(
                    f"composition: domain {d.name.value!r} references unknown channel "
                    f"{ch.value!r}; known: {sorted(channel_to_notifier)}"
                )

    resolver = StaticChannelResolver(channel_to_notifier)

    scheduler = ApsScheduler(timezone=config.runtime.timezone)

    watcher = DomainWatcher(
        repo=repos.domain_repo,
        idempotency=repos.idempotency,
        checker_registry=checker_registry,
        notifier_registry=notifier_registry,
        channel_resolver=resolver,
        notification_policy=policy,
        retry_policy=retry_policy,
        scheduler=scheduler,
        clock=clock,
        event_bus=bus,
        default_thresholds=tuple(config.notification_defaults.thresholds),
        default_schedule="0 */6 * * *",
        initial_domains=initial_domains,
        aclose_hooks=tuple(aclose_hooks),
        learned_rules_repo=repos.learned_rules,
    )
    # Bind the bootstrap factory so the scheduler reconciles from the
    # repository before its first tick.
    scheduler._bootstrap_repo = _BootstrapRepoAdapter(repos.domain_repo)
    scheduler._bootstrap_callable_factory = watcher._make_job_callable
    return watcher


# ---------------------------------------------------------------------------
# State backend selection
# ---------------------------------------------------------------------------


def _build_repos(state_db: str) -> _ComposedRepos:
    """Pick repos based on ``runtime.state_db`` URL."""
    if state_db.startswith("memory://") or state_db == "memory":
        return _ComposedRepos(
            domain_repo=MemoryMonitoredDomainRepo(),
            idempotency=MemoryIdempotencyStore(),
            learned_rules=MemoryLearnedRulesRepo(),
            aclose=None,
        )

    sqla_url = _normalise_sqla_url(state_db)
    engine = create_async_engine(sqla_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Bind one session for the lifetime of the composed watcher. Repos
    # share it; the scheduler ticks are serial per-job so concurrent
    # writes through the same session are bounded by the scheduler's
    # ``max_instances=1`` policy.
    session = _SessionHandle(session_factory)
    repos = _ComposedRepos(
        domain_repo=cast(
            "MonitoredDomainRepository",
            _LazySession(session, lambda s: SqlMonitoredDomainRepo(s)),
        ),
        idempotency=cast(
            "IdempotencyStore",
            _LazySession(session, lambda s: SqlIdempotencyStore(s)),
        ),
        learned_rules=cast(
            "LearnedRulesRepository",
            _LazySession(session, lambda s: SqlLearnedRulesRepo(s)),
        ),
        aclose=session.aclose,
    )
    return repos


def _normalise_sqla_url(url: str) -> str:
    """Coerce ``sqlite:///foo.db`` to ``sqlite+aiosqlite:///foo.db`` etc."""
    if url.startswith("sqlite:///"):
        return "sqlite+aiosqlite:///" + url.removeprefix("sqlite:///")
    if url.startswith("postgresql://") and "+" not in url[: url.index("://")]:
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class _SessionHandle:
    """One ``AsyncSession`` shared by the composed repos.

    Lazily opened on first access; closed by ``aclose``. Composition
    avoids a UoW boundary because scheduler ticks are serialized per
    domain and operations are autocommitted at the engine level.
    """

    __slots__ = ("_factory", "_session")

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory
        self._session: AsyncSession | None = None

    def get(self) -> AsyncSession:
        if self._session is None:
            self._session = self._factory()
        return self._session

    async def aclose(self) -> None:
        sess = self._session
        if sess is None:
            return
        try:
            await sess.commit()
        finally:
            await sess.close()
            self._session = None


class _LazySession:
    """Defer repo construction until first call so the session is open."""

    __slots__ = ("_factory", "_handle", "_repo")

    def __init__(
        self,
        handle: _SessionHandle,
        factory: Callable[[AsyncSession], object],
    ) -> None:
        self._handle = handle
        self._factory = factory
        self._repo: object | None = None

    def _resolve(self) -> Any:
        if self._repo is None:
            self._repo = self._factory(self._handle.get())
        return self._repo

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


# ---------------------------------------------------------------------------
# Parsing service
# ---------------------------------------------------------------------------


def _build_parsing_service(
    *,
    config: Config,
    clock: TimeProvider,
    bus: InProcessEventBus,
    learned_rules: LearnedRulesRepository,
    http_client: httpx.AsyncClient | None,
) -> tuple[ParsingService, Callable[[], Awaitable[None]] | None]:
    parser = RegexWhoisParser()
    static_rules = tuple(_to_parse_rule(r) for r in config.parsing.whois_rules)

    suggester = None
    pipeline = None
    host_lim = None
    tld_lim = None
    aclose: Callable[[], Awaitable[None]] | None = None

    fb = config.parsing.llm_fallback
    if fb.enabled:
        if fb.suggester is None:  # pragma: no cover — schema validator catches this
            raise ConfigError("llm_fallback.suggester required when enabled")
        sug_settings = dict(fb.suggester.settings)
        if fb.suggester.type != "litellm":
            raise ConfigError(
                f"llm_fallback.suggester.type must be 'litellm', got {fb.suggester.type!r}"
            )
        litellm_suggester = LiteLLMRuleSuggester(
            model=str(sug_settings.get("model", "")),
            api_base=sug_settings.get("api_base"),
            api_key=sug_settings.get("api_key"),
            timeout=float(sug_settings.get("timeout", 30.0)),
        )
        if not litellm_suggester.model:
            raise ConfigError("llm_fallback.suggester.settings.model is required")
        breaker = default_circuit_breaker(clock)
        suggester = SuggesterCircuitBreaker(inner=litellm_suggester, breaker=breaker)

        # Pipeline cross-check uses a thin _WhoisFetcher.
        whois_fetcher = _WhoisFetcher(timeout=30.0)
        pipeline = ValidationPipeline(
            cross_check_fetcher=_FetcherToCrossCheck(whois_fetcher),
            clock=clock,
            max_age_years=fb.safety.max_age_years,
            revalidate_after_seconds=fb.safety.revalidate_after.seconds,
        )
        max_per_hour = fb.safety.max_learn_per_hour
        if max_per_hour > 0:
            host_lim = TokenBucketLimiter(capacity=max_per_hour, window_seconds=3600.0, clock=clock)
        max_per_tld = fb.safety.max_learn_per_tld_per_24h
        if max_per_tld > 0:
            tld_lim = PerTldLimiter(capacity=max_per_tld, window_seconds=86400.0, clock=clock)

    parsing_service = ParsingService(
        parser=parser,
        learned_rules=learned_rules,
        publisher=bus,
        clock=clock,
        static_rules=static_rules,
        suggester=suggester,
        validation_pipeline=pipeline,
        host_limiter=host_lim,
        tld_limiter=tld_lim,
        suggester_host="default",
    )
    return parsing_service, aclose


def _to_parse_rule(raw: WhoisRule) -> ParseRule:
    """Coerce a config ``WhoisRule`` to a core ``ParseRule``.

    The config layer holds the regex as a plain string; the core VO
    requires a ``RegexPattern``. Cross-layer mapping lives here, not in
    ``core``.
    """
    return ParseRule(
        tld=raw.tld,
        expires_regex=RegexPattern(raw.expires_regex),
        date_format=DateFormat(raw.date_format),
        timezone=raw.timezone,
        strptime_format=raw.strptime_format,
    )


@dataclass(slots=True)
class _FetcherToCrossCheck:
    """Adapter: ``_WhoisFetcher.fetch()`` → ``CrossCheckFetcher.fetch_raw()`` (raw string).

    Maps the fetcher's ``raw`` field to the cross-check's expected ``str``
    return; raises the same ``Transient/PermanentCheckError`` shape the
    pipeline already handles.
    """

    fetcher: _WhoisFetcher

    async def fetch_raw(self, domain: DomainName) -> str:
        from domain_watcher.core.checking.value_objects import CheckOutcome
        from domain_watcher.core.shared.errors import (
            PermanentCheckError,
            TransientCheckError,
        )

        result = await self.fetcher.fetch(domain)
        if result.raw is not None and result.raw.strip():
            return result.raw
        if result.outcome is CheckOutcome.PERMANENT_ERROR:
            raise PermanentCheckError(result.error or "no_match")
        raise TransientCheckError(result.error or "empty_whois")


# ---------------------------------------------------------------------------
# Checker / notifier factories
# ---------------------------------------------------------------------------


def _build_checker(
    cc: CheckerConfig,
    *,
    parsing_service: ParsingService,
    http_client: httpx.AsyncClient | None,
) -> tuple[ExpirationChecker, Callable[[], Awaitable[None]] | None]:
    factory = _CHECKER_FACTORIES.get(cc.type)
    if factory is None:
        raise ConfigError(
            f"unknown checker type {cc.type!r} for id {cc.id!r}; "
            f"known: {sorted(_CHECKER_FACTORIES)}"
        )
    return factory(cc, parsing_service=parsing_service, http_client=http_client)


def _build_notifier(
    nc: NotifierConfig,
    *,
    http_client: httpx.AsyncClient | None,
) -> tuple[Notifier, Callable[[], Awaitable[None]] | None]:
    factory = _NOTIFIER_FACTORIES.get(nc.type)
    if factory is None:
        raise ConfigError(
            f"unknown notifier type {nc.type!r} for id {nc.id!r}; "
            f"known: {sorted(_NOTIFIER_FACTORIES)}"
        )
    return factory(nc, http_client=http_client)


# ----- checker factories ---------------------------------------------------


def _factory_rdap(
    cc: CheckerConfig,
    *,
    parsing_service: ParsingService,  # unused but in signature for uniformity
    http_client: httpx.AsyncClient | None,
) -> tuple[ExpirationChecker, Callable[[], Awaitable[None]] | None]:
    del parsing_service
    settings = cc.settings or {}
    timeout = _coerce_seconds(settings.get("timeout", 10.0))
    bootstrap_url = settings.get("bootstrap_url")
    client = http_client if http_client is not None else httpx.AsyncClient(timeout=timeout)
    bootstrap = (
        IanaBootstrap(client=client, url=bootstrap_url)
        if bootstrap_url is not None
        else IanaBootstrap(client=client)
    )

    async def _aclose() -> None:
        await bootstrap.aclose()
        if http_client is None:
            await client.aclose()

    checker = RdapChecker(bootstrap=bootstrap, client=client)
    # Register under the configured id, not the hard-coded ClassVar id —
    # composition needs the YAML id (e.g. ``"rdap"``) so cross-references resolve.
    return cast("ExpirationChecker", _IdAlias(checker, cc.id)), _aclose


def _factory_whois(
    cc: CheckerConfig,
    *,
    parsing_service: ParsingService,
    http_client: httpx.AsyncClient | None,
) -> tuple[ExpirationChecker, Callable[[], Awaitable[None]] | None]:
    del http_client
    settings = cc.settings or {}
    timeout = _coerce_seconds(settings.get("timeout", 30.0))
    fetcher = _WhoisFetcher(timeout=timeout)
    composite = WhoisCheckerWithParser(fetcher=fetcher, parse=parsing_service.parse)
    return cast("ExpirationChecker", _IdAlias(composite, cc.id)), None


def _factory_script(
    cc: CheckerConfig,
    *,
    parsing_service: ParsingService,
    http_client: httpx.AsyncClient | None,
) -> tuple[ExpirationChecker, Callable[[], Awaitable[None]] | None]:
    del parsing_service, http_client
    settings = cc.settings or {}
    raw_command = settings.get("command")
    if not isinstance(raw_command, list) or not raw_command:
        raise ConfigError(f"script checker {cc.id!r}: settings.command must be a non-empty list")
    command = tuple(str(p) for p in raw_command)
    timeout = _coerce_seconds(settings.get("timeout", 30.0))
    env = settings.get("env")
    checker = ScriptChecker(
        command=command,
        timeout=timeout,
        env=dict(env) if isinstance(env, dict) else None,
    )
    return cast("ExpirationChecker", _IdAlias(checker, cc.id)), None


_CHECKER_FACTORIES: dict[
    str,
    Callable[..., tuple[ExpirationChecker, Callable[[], Awaitable[None]] | None]],
] = {
    "rdap": _factory_rdap,
    "whois": _factory_whois,
    "script": _factory_script,
}


# ----- notifier factories --------------------------------------------------


def _factory_telegram(
    nc: NotifierConfig,
    *,
    http_client: httpx.AsyncClient | None,
) -> tuple[Notifier, Callable[[], Awaitable[None]] | None]:
    s = nc.settings or {}
    notifier = TelegramNotifier(
        bot_token=str(s.get("bot_token", "")),
        chat_id=str(s.get("chat_id", "")),
        parse_mode=str(s.get("parse_mode", "HTML")),
        api_base=str(s.get("api_base", "https://api.telegram.org")),
        client=http_client,
        timeout=_coerce_seconds(s.get("timeout", 10.0)),
    )
    aclose = notifier.aclose if http_client is None else None
    return cast("Notifier", _IdAlias(notifier, nc.id)), aclose


def _factory_email(
    nc: NotifierConfig,
    *,
    http_client: httpx.AsyncClient | None,
) -> tuple[Notifier, Callable[[], Awaitable[None]] | None]:
    del http_client
    s = nc.settings or {}
    to_addrs_raw = s.get("to_addrs", ())
    if not isinstance(to_addrs_raw, (list, tuple)):
        raise ConfigError(f"email notifier {nc.id!r}: to_addrs must be a list")
    notifier = EmailNotifier(
        smtp_host=str(s.get("smtp_host", "")),
        smtp_port=int(s.get("smtp_port", 587)),
        from_addr=str(s.get("from_addr", "")),
        to_addrs=tuple(str(x) for x in to_addrs_raw),
        username=s.get("username"),
        password=s.get("password"),
        use_starttls=bool(s.get("use_starttls", True)),
        use_tls=bool(s.get("use_tls", False)),
        allow_insecure=bool(s.get("allow_insecure", False)),
        timeout=_coerce_seconds(s.get("timeout", 30.0)),
    )
    return cast("Notifier", _IdAlias(notifier, nc.id)), None


def _factory_discord(
    nc: NotifierConfig,
    *,
    http_client: httpx.AsyncClient | None,
) -> tuple[Notifier, Callable[[], Awaitable[None]] | None]:
    s = nc.settings or {}
    notifier = DiscordNotifier(
        webhook_url=str(s.get("webhook_url", "")),
        username=s.get("username"),
        avatar_url=s.get("avatar_url"),
        client=http_client,
        timeout=_coerce_seconds(s.get("timeout", 10.0)),
    )
    aclose = notifier.aclose if http_client is None else None
    return cast("Notifier", _IdAlias(notifier, nc.id)), aclose


def _factory_webhook(
    nc: NotifierConfig,
    *,
    http_client: httpx.AsyncClient | None,
) -> tuple[Notifier, Callable[[], Awaitable[None]] | None]:
    s = nc.settings or {}
    headers_raw = s.get("headers", {}) or {}
    if not isinstance(headers_raw, dict):
        raise ConfigError(f"webhook notifier {nc.id!r}: headers must be a mapping")
    notifier = WebhookNotifier(
        url=str(s.get("url", "")),
        body_template=str(s.get("body_template", "")),
        method=str(s.get("method", "POST")),
        headers={str(k): str(v) for k, v in headers_raw.items()},
        content_type=str(s.get("content_type", "application/json")),
        client=http_client,
        timeout=_coerce_seconds(s.get("timeout", 10.0)),
    )
    aclose = notifier.aclose if http_client is None else None
    return cast("Notifier", _IdAlias(notifier, nc.id)), aclose


_NOTIFIER_FACTORIES: dict[
    str,
    Callable[..., tuple[Notifier, Callable[[], Awaitable[None]] | None]],
] = {
    "telegram": _factory_telegram,
    "email": _factory_email,
    "discord": _factory_discord,
    "webhook": _factory_webhook,
}


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


class _IdAlias:
    """Wraps an adapter to expose ``id = <config-id>`` while delegating everything else.

    Composition needs the YAML-defined id on the registry, but adapters
    declare ``id: ClassVar[str]`` (e.g. ``"rdap"``). When the YAML uses a
    different id (operator names them ``"rdap-primary"``, ``"rdap-fallback"``),
    we route through this wrapper so the registry stores the operator's id
    and ``check`` / ``send`` are forwarded unchanged.
    """

    __slots__ = ("_inner", "id")

    def __init__(self, inner: object, instance_id: str) -> None:
        self._inner = inner
        self.id = instance_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _coerce_seconds(value: object) -> float:
    """Accept ``int | float | Duration | "30s" | str`` and return float seconds."""
    if isinstance(value, Duration):
        return float(value.seconds)
    if isinstance(value, bool):
        # ``bool`` is an ``int``; refuse explicitly.
        raise ConfigError(f"timeout must be number or duration, got bool: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(Duration.parse(value).seconds)
    raise ConfigError(f"timeout must be number or duration, got {type(value).__name__}")


def _build_domain(d: DomainEntry, *, defaults: tuple[Duration, ...]) -> MonitoredDomain:
    """Project ``DomainEntry`` to ``MonitoredDomain``."""
    name = DomainName(d.name)
    schedule = CheckSchedule(d.schedule)
    thresholds = tuple(d.thresholds) if d.thresholds is not None else defaults
    channels = tuple(ChannelId(c) for c in d.channels)
    return MonitoredDomain(
        name=name,
        schedule=schedule,
        checker_id=d.checker,
        notify_thresholds=thresholds,
        channels=channels,
        last_check=None,
        metadata=dict(d.metadata),
    )


@dataclass(slots=True)
class _BootstrapRepoAdapter:
    """Adapter the scheduler bootstraps from. Narrow to ``list_all`` only."""

    repo: Any

    async def list_all(self) -> Sequence[MonitoredDomain]:
        return await self.repo.list_all()


__all__ = ["compose_from_config"]
