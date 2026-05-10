"""Reconciliation subscribers — diff old vs new config and reconcile.

Covers (ADR 0003 §5 + plan task 7.4):

- :class:`SchedulerSubscriber` calls ``scheduler.reconcile`` with the new
  domain set; unchanged domains keep their schedule.
- :class:`RegistrySubscriber` adds, removes, replaces, and (when a
  ``reload_hook`` accepts) hot-reloads plugin instances.
- :class:`ParsingSubscriber` atomically swaps the rule set.
- Removing a notifier id drains in-flight ``send`` calls on the OLD
  instance while new dispatch attempts to that id raise
  ``PluginNotFoundError``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from domain_watcher.application.scheduling import MemoryScheduler
from domain_watcher.application.use_cases.reload_config import (
    ConfigHolder,
    ParsingSubscriber,
    PluginSpec,
    RegistrySubscriber,
    SchedulerSubscriber,
)
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import ChannelId, CheckSchedule
from domain_watcher.core.shared.value_objects import DomainName, Duration
from domain_watcher.infrastructure.registry import (
    PluginNotFoundError,
    Registry,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _domain(
    name: str, *, cron: str = "0 */6 * * *", checker: str = "rdap"
) -> MonitoredDomain:
    return MonitoredDomain(
        name=DomainName(name),
        schedule=CheckSchedule(cron=cron),
        checker_id=checker,
        notify_thresholds=(Duration.days(30), Duration.days(7), Duration.days(1)),
        channels=(ChannelId("tg-ops"),),
    )


@dataclass(frozen=True)
class _Cfg:
    """Small config-shaped stand-in for these tests."""

    domains: tuple[MonitoredDomain, ...] = ()
    plugins: tuple[PluginSpec, ...] = ()
    rules: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# SchedulerSubscriber
# ---------------------------------------------------------------------------
async def test_scheduler_subscriber_reconciles_added_removed_unchanged() -> None:
    scheduler = MemoryScheduler()

    async def _job() -> None:
        return None

    sub = SchedulerSubscriber[_Cfg](
        scheduler=scheduler,
        domains_of=lambda c: c.domains,
        callable_factory=lambda _d: _job,
    )
    holder: ConfigHolder[_Cfg] = ConfigHolder()
    holder.subscribe_object(sub)

    a = _domain("a.example.com")
    b = _domain("b.example.com")
    c = _domain("c.example.com")
    await holder.update(_Cfg(domains=(a, b)))
    assert set(scheduler.list_jobs()) == {"check:a.example.com", "check:b.example.com"}

    # Unchanged a, swap b→c.
    await holder.update(_Cfg(domains=(a, c)))
    assert set(scheduler.list_jobs()) == {"check:a.example.com", "check:c.example.com"}


async def test_scheduler_subscriber_changed_schedule_replaces_job() -> None:
    scheduler = MemoryScheduler()

    async def _job() -> None:
        return None

    sub = SchedulerSubscriber[_Cfg](
        scheduler=scheduler,
        domains_of=lambda c: c.domains,
        callable_factory=lambda _d: _job,
    )
    holder: ConfigHolder[_Cfg] = ConfigHolder()
    holder.subscribe_object(sub)

    d1 = _domain("a.example.com", cron="0 */6 * * *")
    await holder.update(_Cfg(domains=(d1,)))
    assert scheduler.jobs["check:a.example.com"][0] == "0 */6 * * *"

    d2 = _domain("a.example.com", cron="0 */1 * * *")
    await holder.update(_Cfg(domains=(d2,)))
    assert scheduler.jobs["check:a.example.com"][0] == "0 */1 * * *"


# ---------------------------------------------------------------------------
# RegistrySubscriber: built around the real infrastructure.Registry so we
# exercise the actual registration semantics, including drain-on-remove.
# ---------------------------------------------------------------------------
@dataclass
class _FakeNotifier:
    """Minimal Registry-shaped instance with mutable settings + send hook."""

    id: str
    settings: dict[str, Any] = field(default_factory=dict)
    sent: list[str] = field(default_factory=list)
    block: asyncio.Event | None = None

    async def send(self, payload: str) -> None:
        if self.block is not None:
            await self.block.wait()
        self.sent.append(payload)

    async def reload(self, settings: dict[str, Any]) -> bool:
        # Hot-reload contract: only secret rotations (token-only diff) are
        # absorbed in-place. Anything else falls through to replace.
        old = dict(self.settings)
        self.settings = dict(settings)
        diff = set(old) ^ set(settings)
        for k in set(old) & set(settings):
            if old[k] != settings[k]:
                diff.add(k)
        return diff <= {"bot_token"}


def _wire_registry_subscriber(
    registry: Registry[_FakeNotifier],
) -> RegistrySubscriber[_Cfg, _FakeNotifier]:
    instances_by_id: dict[str, _FakeNotifier] = {}

    def _factory(spec: PluginSpec) -> _FakeNotifier:
        return _FakeNotifier(id=spec.id, settings=dict(spec.settings))

    def _register(inst: _FakeNotifier) -> None:
        registry.register(inst)
        instances_by_id[inst.id] = inst

    def _unregister(plugin_id: str) -> None:
        # Registry doesn't ship a public unregister yet — pop from internal
        # store. New ``registry.get(id)`` calls will raise PluginNotFoundError.
        registry._items.pop(plugin_id, None)
        instances_by_id.pop(plugin_id, None)

    def _replace(plugin_id: str, inst: _FakeNotifier) -> None:
        registry._items[plugin_id] = inst
        instances_by_id[plugin_id] = inst

    async def _reload(inst: _FakeNotifier, settings: Any) -> bool:
        return await inst.reload(settings)

    return RegistrySubscriber[_Cfg, _FakeNotifier](
        plugins_of=lambda c: c.plugins,
        instance_of=lambda plugin_id: instances_by_id.get(plugin_id),
        factory=_factory,
        register=_register,
        unregister=_unregister,
        replace=_replace,
        reload_hook=_reload,
    )


async def test_registry_subscriber_adds_new_plugins() -> None:
    registry: Registry[_FakeNotifier] = Registry()
    sub = _wire_registry_subscriber(registry)
    holder: ConfigHolder[_Cfg] = ConfigHolder()
    holder.subscribe_object(sub)

    await holder.update(
        _Cfg(
            plugins=(PluginSpec(id="tg", type="telegram", settings={"bot_token": "x"}),)
        )
    )
    assert registry.get("tg").id == "tg"


async def test_registry_subscriber_removes_dropped_plugins() -> None:
    registry: Registry[_FakeNotifier] = Registry()
    sub = _wire_registry_subscriber(registry)
    holder: ConfigHolder[_Cfg] = ConfigHolder()
    holder.subscribe_object(sub)

    await holder.update(
        _Cfg(
            plugins=(
                PluginSpec(id="tg", type="telegram", settings={"bot_token": "x"}),
                PluginSpec(id="discord", type="discord", settings={"webhook_url": "u"}),
            )
        )
    )
    await holder.update(
        _Cfg(
            plugins=(
                PluginSpec(id="discord", type="discord", settings={"webhook_url": "u"}),
            )
        )
    )

    import pytest

    with pytest.raises(PluginNotFoundError):
        registry.get("tg")
    assert registry.get("discord").id == "discord"


async def test_registry_subscriber_unchanged_plugin_is_no_op() -> None:
    registry: Registry[_FakeNotifier] = Registry()
    sub = _wire_registry_subscriber(registry)
    holder: ConfigHolder[_Cfg] = ConfigHolder()
    holder.subscribe_object(sub)

    spec = PluginSpec(id="tg", type="telegram", settings={"bot_token": "x"})
    await holder.update(_Cfg(plugins=(spec,)))
    original = registry.get("tg")

    # Same exact spec → instance must NOT be re-instantiated.
    await holder.update(_Cfg(plugins=(spec,)))
    assert registry.get("tg") is original


async def test_registry_subscriber_settings_change_replaces_instance() -> None:
    registry: Registry[_FakeNotifier] = Registry()
    sub = _wire_registry_subscriber(registry)
    sub_no_hook = RegistrySubscriber(
        plugins_of=sub.plugins_of,
        instance_of=sub.instance_of,
        factory=sub.factory,
        register=sub.register,
        unregister=sub.unregister,
        replace=sub.replace,
        reload_hook=None,  # disable hot-reload entirely
    )
    holder: ConfigHolder[_Cfg] = ConfigHolder()
    holder.subscribe_object(sub_no_hook)

    await holder.update(
        _Cfg(plugins=(PluginSpec(id="tg", type="telegram", settings={"chat_id": "1"}),))
    )
    original = registry.get("tg")

    await holder.update(
        _Cfg(plugins=(PluginSpec(id="tg", type="telegram", settings={"chat_id": "2"}),))
    )
    new = registry.get("tg")
    assert new is not original
    assert new.settings == {"chat_id": "2"}


async def test_registry_subscriber_secret_only_diff_uses_hot_reload() -> None:
    registry: Registry[_FakeNotifier] = Registry()
    sub = _wire_registry_subscriber(registry)
    holder: ConfigHolder[_Cfg] = ConfigHolder()
    holder.subscribe_object(sub)

    await holder.update(
        _Cfg(
            plugins=(
                PluginSpec(id="tg", type="telegram", settings={"bot_token": "v1"}),
            )
        )
    )
    original = registry.get("tg")

    await holder.update(
        _Cfg(
            plugins=(
                PluginSpec(id="tg", type="telegram", settings={"bot_token": "v2"}),
            )
        )
    )
    after = registry.get("tg")
    assert after is original  # in-place reload kept the instance
    assert after.settings["bot_token"] == "v2"


async def test_registry_subscriber_type_change_forces_replace() -> None:
    registry: Registry[_FakeNotifier] = Registry()
    sub = _wire_registry_subscriber(registry)
    holder: ConfigHolder[_Cfg] = ConfigHolder()
    holder.subscribe_object(sub)

    await holder.update(
        _Cfg(
            plugins=(PluginSpec(id="x", type="telegram", settings={"bot_token": "v"}),)
        )
    )
    original = registry.get("x")

    await holder.update(
        _Cfg(plugins=(PluginSpec(id="x", type="discord", settings={"bot_token": "v"}),))
    )
    after = registry.get("x")
    assert after is not original


async def test_in_flight_send_drains_on_old_instance_after_removal() -> None:
    """Notifier id removed → in-flight send completes on old; new lookup raises."""
    registry: Registry[_FakeNotifier] = Registry()
    sub = _wire_registry_subscriber(registry)
    holder: ConfigHolder[_Cfg] = ConfigHolder()
    holder.subscribe_object(sub)

    await holder.update(
        _Cfg(
            plugins=(PluginSpec(id="tg", type="telegram", settings={"bot_token": "v"}),)
        )
    )
    notifier = registry.get("tg")
    barrier = asyncio.Event()
    notifier.block = barrier  # make send() block mid-call

    async def _send() -> None:
        await notifier.send("alert-1")  # bound to OLD instance reference

    in_flight = asyncio.create_task(_send())
    await asyncio.sleep(0)  # let the task start

    # Remove the notifier from config.
    await holder.update(_Cfg(plugins=()))

    # New dispatch attempts to id "tg" raise.
    import pytest

    with pytest.raises(PluginNotFoundError):
        registry.get("tg")

    # In-flight send is still alive — the old instance reference works.
    assert not in_flight.done()
    barrier.set()
    await in_flight
    assert notifier.sent == ["alert-1"]


# ---------------------------------------------------------------------------
# ParsingSubscriber
# ---------------------------------------------------------------------------
async def test_parsing_subscriber_atomic_swap() -> None:
    holder_state: list[tuple[str, ...]] = [()]

    async def _apply(rules: Sequence[str]) -> None:
        holder_state[0] = tuple(rules)

    sub = ParsingSubscriber[_Cfg, str](
        rules_of=lambda c: c.rules,
        apply=_apply,
    )
    holder: ConfigHolder[_Cfg] = ConfigHolder()
    holder.subscribe_object(sub)

    await holder.update(_Cfg(rules=("ru-rule", "com-rule")))
    assert holder_state[0] == ("ru-rule", "com-rule")

    await holder.update(_Cfg(rules=("io-rule",)))
    assert holder_state[0] == ("io-rule",)

    # Empty rule set is a legal swap.
    await holder.update(_Cfg(rules=()))
    assert holder_state[0] == ()
