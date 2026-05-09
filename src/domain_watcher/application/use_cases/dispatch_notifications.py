"""``DispatchNotificationsUseCase`` — turn a check result into delivered alerts.

Flow (plan Task 2.6):

```
on DomainCheckCompleted(result):
  alerts = policy.alerts_for(prev, result, now)
  for alert in alerts:
    for channel in resolver.channels_for(domain):
      if idempotency.already_fired(...): skip
      try:
        retry-ed notifier.send(alert, channel)
        idempotency.record(...)
        publish NotificationDispatched
      except DeliveryFailedError after retries:
        publish NotificationFailed
```

Per-channel coroutines are gathered with ``return_exceptions=True`` so a
permanent failure on one channel does NOT cancel sibling deliveries (plan
Task 2.6 explicitly forbids this footgun).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain_watcher.core.notification.events import (
    NotificationDispatched,
    NotificationFailed,
)
from domain_watcher.core.shared.errors import (
    DeliveryFailedError,
    NotificationError,
)

if TYPE_CHECKING:
    from domain_watcher.core.checking.policies import RetryPolicy
    from domain_watcher.core.checking.value_objects import CheckResult
    from domain_watcher.core.monitoring.entities import MonitoredDomain
    from domain_watcher.core.monitoring.value_objects import LastCheck
    from domain_watcher.core.notification.entities import Alert, Channel
    from domain_watcher.core.notification.policies import NotificationPolicy
    from domain_watcher.core.notification.ports import (
        ChannelResolver,
        IdempotencyStore,
        Notifier,
    )
    from domain_watcher.core.shared.events import EventPublisher
    from domain_watcher.core.shared.time_provider import TimeProvider


Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DispatchNotificationsUseCase:
    policy: NotificationPolicy
    resolver: ChannelResolver
    idempotency: IdempotencyStore
    notifiers: Mapping[str, Notifier]
    publisher: EventPublisher
    clock: TimeProvider
    retry_policy: RetryPolicy
    sleeper: Sleeper = asyncio.sleep

    async def execute(
        self,
        domain: MonitoredDomain,
        previous: LastCheck | None,
        current: CheckResult,
    ) -> None:
        alerts = self.policy.alerts_for(previous, current, self.clock.now())
        if not alerts:
            return
        channels = await self.resolver.channels_for(domain)
        if not channels:
            return

        tasks = [
            asyncio.create_task(self._deliver(alert, channel))
            for alert in alerts
            for channel in channels
        ]
        # return_exceptions=True keeps siblings alive when one task hits an
        # unexpected exception (handled paths emit NotificationFailed and
        # return cleanly).
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _deliver(self, alert: Alert, channel: Channel) -> None:
        if await self.idempotency.already_fired(
            alert.domain, alert.threshold, alert.cycle_id, channel.id
        ):
            return
        notifier = self.notifiers.get(channel.notifier_id)
        if notifier is None:
            await self.publisher.publish(
                NotificationFailed(
                    occurred_at=self.clock.now(),
                    alert=alert,
                    channel=channel.id,
                    reason=f"unknown notifier id {channel.notifier_id!r}",
                    attempts=1,
                )
            )
            return

        last_reason = ""
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                await notifier.send(alert, channel)
            except DeliveryFailedError as exc:
                last_reason = str(exc) or "delivery_failed"
                if attempt < self.retry_policy.max_attempts:
                    delay = self.retry_policy.delay_for(attempt).seconds
                    await self.sleeper(delay)
                    continue
                await self.publisher.publish(
                    NotificationFailed(
                        occurred_at=self.clock.now(),
                        alert=alert,
                        channel=channel.id,
                        reason=last_reason,
                        attempts=attempt,
                    )
                )
                return
            except NotificationError as exc:
                # Permanent transport failure: do NOT retry, surface and stop.
                await self.publisher.publish(
                    NotificationFailed(
                        occurred_at=self.clock.now(),
                        alert=alert,
                        channel=channel.id,
                        reason=str(exc) or "permanent_error",
                        attempts=attempt,
                    )
                )
                return
            else:
                await self.idempotency.record(
                    alert.domain,
                    alert.threshold,
                    alert.cycle_id,
                    channel.id,
                    self.clock.now(),
                )
                await self.publisher.publish(
                    NotificationDispatched(
                        occurred_at=self.clock.now(),
                        alert=alert,
                        channel=channel.id,
                    )
                )
                return


__all__ = ["DispatchNotificationsUseCase"]
