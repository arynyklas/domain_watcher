"""StaticChannelResolver: lookup hits + missing-id reports the missing id."""

from __future__ import annotations

import pytest

from domain_watcher.application.channel_resolver import StaticChannelResolver
from domain_watcher.core.monitoring.entities import MonitoredDomain
from domain_watcher.core.monitoring.value_objects import ChannelId, CheckSchedule
from domain_watcher.core.shared.value_objects import DomainName, Duration


def _domain(channels: tuple[str, ...]) -> MonitoredDomain:
    return MonitoredDomain(
        name=DomainName("a.com"),
        schedule=CheckSchedule("0 */6 * * *"),
        checker_id="rdap",
        notify_thresholds=(Duration.days(30), Duration.days(1)),
        channels=tuple(ChannelId(c) for c in channels),
    )


async def test_known_channels_resolved() -> None:
    r = StaticChannelResolver({"tg-ops": "telegram", "email-team": "email"})
    out = await r.channels_for(_domain(("tg-ops", "email-team")))
    assert [c.id.value for c in out] == ["tg-ops", "email-team"]
    assert [c.notifier_id for c in out] == ["telegram", "email"]


async def test_unknown_channel_id_raises_with_id() -> None:
    r = StaticChannelResolver({"tg-ops": "telegram"})
    with pytest.raises(KeyError, match="missing-channel"):
        await r.channels_for(_domain(("missing-channel",)))
