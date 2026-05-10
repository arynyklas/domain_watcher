"""Repository contract suite (ADR 0002 §2/§4/§5).

Plugin authors of new repository back-ends subclass
:class:`RepoContractTest`, override :meth:`make_repos` to yield an
``(monitored, learned, idempotency)`` triple bound to a fresh backend,
and pytest will run the conformance suite.

The base class is intentionally backend-agnostic — it makes no
assumption about transactions, async vs sync, or schema migrations.
The test methods only call the repository ports.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import ChannelId, CheckSchedule
from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.value_objects import DomainName, Duration

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence


_NOW = datetime(2026, 5, 9, tzinfo=UTC)
_EXPIRES = datetime(2027, 1, 1, tzinfo=UTC)


def _domain(name: str = "example.com") -> MonitoredDomain:
    return MonitoredDomain(
        name=DomainName(name),
        checker_id="rdap",
        schedule=CheckSchedule("0 */6 * * *"),
        notify_thresholds=(Duration.days(30), Duration.days(7), Duration.days(1)),
        channels=(ChannelId("primary"),),
        last_check=None,
    )


class RepoContractTest:
    """Base class — subclass and override :meth:`make_repos`.

    The factory MUST be an async context manager that yields a 3-tuple of
    ``(MonitoredDomainRepository, LearnedRulesRepository, IdempotencyStore)``.
    The harness handles its lifecycle.
    """

    @asynccontextmanager
    async def make_repos(self) -> AsyncIterator[tuple[Any, Any, Any]]:
        """Override to yield ``(monitored, learned, idempotency)``."""

        raise NotImplementedError(
            "Override make_repos() — see RepoContractTest docstring."
        )
        yield  # pragma: no cover — keeps the function a generator for type-check

    @pytest.mark.asyncio
    async def test_monitored_add_get_remove(self) -> None:
        async with self.make_repos() as (mon, _learned, _idem):
            d = _domain()
            await mon.add(d)
            got = await mon.get(d.name)
            assert got is not None
            assert got.name == d.name
            await mon.remove(d.name)
            assert await mon.get(d.name) is None

    @pytest.mark.asyncio
    async def test_monitored_remove_missing_is_noop(self) -> None:
        async with self.make_repos() as (mon, _learned, _idem):
            await mon.remove(DomainName("never-added.com"))

    @pytest.mark.asyncio
    async def test_monitored_list_all(self) -> None:
        async with self.make_repos() as (mon, _learned, _idem):
            await mon.add(_domain("a.com"))
            await mon.add(_domain("b.com"))
            all_: Sequence[MonitoredDomain] = await mon.list_all()
            names = {d.name.value for d in all_}
            assert names == {"a.com", "b.com"}

    @pytest.mark.asyncio
    async def test_learned_rules_lifecycle(self) -> None:
        rule = ParseRule(
            tld="example",
            expires_regex=RegexPattern(raw=r"Expires:\s*(\S+)"),
            date_format=DateFormat.ISO_8601,
            timezone="UTC",
        )
        async with self.make_repos() as (_mon, learned, _idem):
            row_id = await learned.add(
                rule,
                sample_sha256="0" * 64,
                sample_domain=DomainName("example.com"),
                suggester_id="test",
                pipeline_version=1,
            )
            assert isinstance(row_id, int)

            rules = await learned.for_tld("example")
            assert len(rules) == 1
            assert rules[0].tld == "example"

            await learned.disable(row_id, reason="contract-test")
            assert await learned.for_tld("example") == ()

    @pytest.mark.asyncio
    async def test_idempotency_4tuple_keys(self) -> None:
        async with self.make_repos() as (_mon, _learned, idem):
            d = DomainName("example.com")
            t = Duration.days(7)
            cycle_a = "a" * 16
            cycle_b = "b" * 16
            ch_x = ChannelId("telegram")
            ch_y = ChannelId("email")

            assert not await idem.already_fired(d, t, cycle_a, ch_x)
            await idem.record(d, t, cycle_a, ch_x, _NOW)
            assert await idem.already_fired(d, t, cycle_a, ch_x)

            # Different cycle ⇒ not deduped.
            assert not await idem.already_fired(d, t, cycle_b, ch_x)
            # Different channel ⇒ not deduped.
            assert not await idem.already_fired(d, t, cycle_a, ch_y)


# ``_EXPIRES`` is exported for subclasses that want to thread the same
# constants through their assertions.
__all__ = ["RepoContractTest"]


# Suppress an unused-import lint for ``_EXPIRES`` until a subclass uses it.
_ = _EXPIRES
