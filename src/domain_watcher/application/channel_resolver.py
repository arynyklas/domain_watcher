"""``ChannelResolver`` implementations.

The default ``StaticChannelResolver`` looks up each ``ChannelId`` listed
on a ``MonitoredDomain`` in a ``NotifierRegistry`` and produces one
``Channel`` per id with empty ``routing`` (the static config is fully
specified at construction time on the notifier itself).

The bot ships a tenant-aware variant; that lives in the bot repo, not
here. The use case (``DispatchNotificationsUseCase``) consumes this port
without caring about the impl.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain_watcher.core.notification.entities import Channel

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.monitoring.value_objects import ChannelId


@dataclass(frozen=True, slots=True)
class StaticChannelResolver:
    """Maps ``ChannelId`` → ``notifier_id`` from a precomputed dict.

    The dict is supplied at composition time by the registry wiring code
    in ``infrastructure/`` (the resolver itself stays application-pure).
    """

    notifier_id_by_channel: Mapping[str, str]

    async def channels_for(self, domain: MonitoredDomain) -> Sequence[Channel]:
        out: list[Channel] = []
        for cid in domain.channels:
            notifier_id = self._lookup(cid)
            out.append(Channel(id=cid, notifier_id=notifier_id, routing={}))
        return tuple(out)

    def _lookup(self, cid: ChannelId) -> str:
        try:
            return self.notifier_id_by_channel[cid.value]
        except KeyError as exc:
            raise KeyError(
                f"StaticChannelResolver: unknown channel id {cid.value!r}; "
                f"known: {sorted(self.notifier_id_by_channel)}"
            ) from exc


__all__ = ["StaticChannelResolver"]
