from __future__ import annotations

from typing import TYPE_CHECKING

from domain_watcher.core.notification.ports import ChannelResolver, Notifier

if TYPE_CHECKING:
    from collections.abc import Sequence

    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.notification.entities import Alert, Channel


class _FakeNotifier:
    id = "fake"

    async def send(self, alert: Alert, channel: Channel) -> None:
        del alert, channel


class _FakeResolver:
    async def channels_for(self, domain: MonitoredDomain) -> Sequence[Channel]:
        del domain
        return []


def test_notifier_protocol() -> None:
    assert isinstance(_FakeNotifier(), Notifier)


def test_channel_resolver_protocol() -> None:
    assert isinstance(_FakeResolver(), ChannelResolver)
