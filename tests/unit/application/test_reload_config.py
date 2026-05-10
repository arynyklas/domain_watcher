"""ConfigHolder: atomic swap, fan-out, exception isolation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain_watcher.application.use_cases.reload_config import ConfigHolder

if TYPE_CHECKING:
    import pytest


@dataclass(frozen=True)
class StubConfig:
    name: str
    secret: str = ""


async def test_initial_none_then_update() -> None:
    holder: ConfigHolder[StubConfig] = ConfigHolder()
    seen: list[tuple[StubConfig | None, StubConfig]] = []

    async def sub(old: StubConfig | None, new: StubConfig) -> None:
        seen.append((old, new))

    holder.subscribe(sub)
    new = StubConfig(name="v1")
    await holder.update(new)
    assert holder.current is new
    assert seen == [(None, new)]


async def test_subscriber_exception_isolated(caplog: pytest.LogCaptureFixture) -> None:
    holder: ConfigHolder[StubConfig] = ConfigHolder()
    seen: list[StubConfig] = []

    async def bad(old, new) -> None:
        raise RuntimeError("boom")

    async def good(old, new) -> None:
        seen.append(new)

    holder.subscribe(bad)
    holder.subscribe(good)
    new = StubConfig(name="v1")
    with caplog.at_level(logging.ERROR):
        await holder.update(new)
    assert seen == [new]
    assert any("subscriber raised" in r.message for r in caplog.records)


async def test_subscribe_object_protocol() -> None:
    holder: ConfigHolder[StubConfig] = ConfigHolder()
    seen: list[StubConfig] = []

    class Sub:
        async def on_config_changed(
            self, old: StubConfig | None, new: StubConfig
        ) -> None:
            seen.append(new)

    holder.subscribe_object(Sub())
    new = StubConfig(name="v1")
    await holder.update(new)
    assert seen == [new]


async def test_swap_visible_atomically() -> None:
    holder: ConfigHolder[StubConfig] = ConfigHolder(initial=StubConfig(name="v0"))
    captured_during_callback: list[StubConfig | None] = []

    async def sub(old, new) -> None:
        captured_during_callback.append(holder.current)

    holder.subscribe(sub)
    new = StubConfig(name="v1")
    await holder.update(new)
    # Callback observes the new value (swap happens before fan-out).
    assert captured_during_callback == [new]
