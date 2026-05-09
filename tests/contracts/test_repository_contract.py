"""Shared contract suite — every repo implementation MUST satisfy these.

Parametrized over (memory, sqlite-async). Postgres lives behind
``pytest.mark.integration`` and ``--integration`` because it needs Docker.

Future plugin authors run this against their adapters by extending the
factory list (or copying the class).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import pytest

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import ChannelId, CheckSchedule
from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.persistence.memory import (
    MemoryIdempotencyStore,
    MemoryLearnedRulesRepo,
    MemoryMonitoredDomainRepo,
)
from domain_watcher.infrastructure.persistence.sql import Base, SqlUnitOfWork
from domain_watcher.infrastructure.persistence.sql.repos import (
    SqlIdempotencyStore,
    SqlLearnedRulesRepo,
    SqlMonitoredDomainRepo,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

NOW = datetime(2026, 5, 9, tzinfo=UTC)
EXPIRES = datetime(2027, 1, 1, tzinfo=UTC)


class _MonitoredFactory(Protocol):
    def __call__(self) -> AsyncIterator[object]: ...


def _domain(name: str = "example.com") -> MonitoredDomain:
    return MonitoredDomain(
        name=DomainName(name),
        schedule=CheckSchedule("0 */6 * * *"),
        checker_id="rdap",
        notify_thresholds=(Duration.days(30), Duration.days(1)),
        channels=(ChannelId("tg-ops"),),
    )


# --- Backend factories -------------------------------------------------------


@asynccontextmanager
async def memory_repos():
    yield (
        MemoryMonitoredDomainRepo(),
        MemoryLearnedRulesRepo(),
        MemoryIdempotencyStore(),
        None,  # uow handled implicitly
    )


@asynccontextmanager
async def sqlite_repos():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    uow = SqlUnitOfWork(sessionmaker)
    async with uow:
        yield (
            SqlMonitoredDomainRepo(uow.session),
            SqlLearnedRulesRepo(uow.session),
            SqlIdempotencyStore(uow.session),
            uow,
        )
    await engine.dispose()


@asynccontextmanager
async def postgres_repos():  # pragma: no cover — only when LIVE_PG=1
    try:
        import importlib

        tc_module = importlib.import_module("testcontainers.postgres")
    except ImportError:
        pytest.skip("testcontainers not installed")
        return
    PostgresContainer = tc_module.PostgresContainer
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        uow = SqlUnitOfWork(sessionmaker)
        async with uow:
            yield (
                SqlMonitoredDomainRepo(uow.session),
                SqlLearnedRulesRepo(uow.session),
                SqlIdempotencyStore(uow.session),
                uow,
            )
        await engine.dispose()


_BACKENDS = [
    pytest.param("memory", id="memory"),
    pytest.param("sqlite", id="sqlite"),
]
if os.environ.get("LIVE_PG") == "1":  # pragma: no cover
    _BACKENDS.append(pytest.param("postgres", id="postgres", marks=pytest.mark.integration))


def _factory(name: str):
    if name == "memory":
        return memory_repos
    if name == "sqlite":
        return sqlite_repos
    if name == "postgres":
        return postgres_repos
    raise ValueError(name)


# --- Tests -------------------------------------------------------------------


@pytest.mark.parametrize("backend", _BACKENDS)
async def test_monitored_add_get_remove(backend: str) -> None:
    async with _factory(backend)() as (mon, _, _idem, uow):
        domain = _domain()
        await mon.add(domain)
        if uow is not None:
            await uow.commit()
        out = await mon.get(domain.name)
        assert out is not None
        assert out.name.value == "example.com"
        await mon.remove(domain.name)
        if uow is not None:
            await uow.commit()
        assert await mon.get(domain.name) is None


@pytest.mark.parametrize("backend", _BACKENDS)
async def test_monitored_update_persists_last_check(backend: str) -> None:
    async with _factory(backend)() as (mon, _, _idem, uow):
        domain = _domain()
        await mon.add(domain)
        new = domain.with_check_result(
            CheckResult(
                domain=domain.name,
                outcome=CheckOutcome.OK,
                expires_at=EXPIRES,
                source="rdap",
            ),
            at=NOW,
        )
        await mon.update(new)
        if uow is not None:
            await uow.commit()
        out = await mon.get(domain.name)
        assert out is not None
        assert out.last_check is not None
        assert out.last_check.expires_at == EXPIRES


@pytest.mark.parametrize("backend", _BACKENDS)
async def test_monitored_list_all(backend: str) -> None:
    async with _factory(backend)() as (mon, _, _idem, uow):
        await mon.add(_domain("a.com"))
        await mon.add(_domain("b.com"))
        if uow is not None:
            await uow.commit()
        names = {d.name.value for d in await mon.list_all()}
        assert names == {"a.com", "b.com"}


@pytest.mark.parametrize("backend", _BACKENDS)
async def test_learned_rules_lifecycle(backend: str) -> None:
    rule = ParseRule(
        tld="com",
        expires_regex=RegexPattern(r"Registry Expiry Date:\s*(\S+)"),
        date_format=DateFormat.ISO_8601,
    )
    async with _factory(backend)() as (_mon, learned, _idem, uow):
        rid = await learned.add(
            rule,
            sample_sha256="0" * 64,
            sample_domain=DomainName("example.com"),
            suggester_id="fake",
            pipeline_version=1,
        )
        if uow is not None:
            await uow.commit()
        assert rid >= 1
        rules = await learned.for_tld("com")
        assert len(rules) == 1
        # Duplicate → ValueError
        with pytest.raises(ValueError):
            await learned.add(
                rule,
                sample_sha256="0" * 64,
                sample_domain=DomainName("example.com"),
                suggester_id="fake",
                pipeline_version=1,
            )
        await learned.disable(rid, "stale")
        if uow is not None:
            await uow.commit()
        assert await learned.for_tld("com") == ()


@pytest.mark.parametrize("backend", _BACKENDS)
async def test_learned_rules_mark_revalidated(backend: str) -> None:
    rule = ParseRule(
        tld="net",
        expires_regex=RegexPattern(r"Registry Expiry Date:\s*(\S+)"),
        date_format=DateFormat.ISO_8601,
    )
    async with _factory(backend)() as (_mon, learned, _idem, uow):
        rid = await learned.add(
            rule,
            sample_sha256="0" * 64,
            sample_domain=DomainName("example.net"),
            suggester_id="fake",
            pipeline_version=1,
        )
        if uow is not None:
            await uow.commit()
        await learned.mark_revalidated(rid, NOW)
        if uow is not None:
            await uow.commit()
        out = await learned.list_all()
        assert out[0].last_revalidated_at == NOW
        assert out[0].revalidation_count == 1


@pytest.mark.parametrize("backend", _BACKENDS)
async def test_idempotency_4tuple_key(backend: str) -> None:
    async with _factory(backend)() as (_mon, _learned, idem, uow):
        d = DomainName("example.com")
        t = Duration.days(30)
        cycle_a = "a" * 16
        cycle_b = "b" * 16
        ch_x = ChannelId("tg-ops")
        ch_y = ChannelId("email-team")
        await idem.record(d, t, cycle_a, ch_x, NOW)
        if uow is not None:
            await uow.commit()
        assert await idem.already_fired(d, t, cycle_a, ch_x)
        # Different cycle → distinct
        assert not await idem.already_fired(d, t, cycle_b, ch_x)
        # Different channel → distinct
        assert not await idem.already_fired(d, t, cycle_a, ch_y)
