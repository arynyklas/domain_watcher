"""``CheckDomainUseCase`` — orchestrates a single domain check.

Steps (ADR 0002 §3 + plan Task 2.3):

1. Fetch the ``MonitoredDomain`` from the repo (KeyError if missing).
2. Resolve the ``ExpirationChecker`` by id (KeyError if missing).
3. Apply ``RetryPolicy`` against ``TransientCheckError`` /
   ``CheckOutcome.TRANSIENT_ERROR``.
4. On success: persist via ``repo.update`` and publish
   ``DomainCheckCompleted``.
5. On exhaustion / permanent error: publish ``DomainCheckFailed``.

The use case is purely orchestrational. The checker, repo, clock, sleeper,
and event publisher are injected; ``asyncio.sleep`` is the default sleeper
so production wiring needs no extra plumbing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain_watcher.core.checking.events import (
    DomainCheckCompleted,
    DomainCheckFailed,
)
from domain_watcher.core.checking.value_objects import CheckOutcome
from domain_watcher.core.shared.errors import (
    PermanentCheckError,
    TransientCheckError,
)

if TYPE_CHECKING:
    from domain_watcher.core.checking.policies import RetryPolicy
    from domain_watcher.core.checking.ports import ExpirationChecker
    from domain_watcher.core.checking.value_objects import CheckResult
    from domain_watcher.core.monitoring.ports import MonitoredDomainRepository
    from domain_watcher.core.shared.events import EventPublisher
    from domain_watcher.core.shared.time_provider import TimeProvider
    from domain_watcher.core.shared.value_objects import DomainName


Sleeper = Callable[[float], Awaitable[None]]


class DomainNotMonitoredError(LookupError):
    """Repo has no ``MonitoredDomain`` for the requested name."""


class CheckerNotRegisteredError(LookupError):
    """No ``ExpirationChecker`` is registered for the requested id."""


@dataclass(frozen=True, slots=True)
class CheckDomainUseCase:
    repo: MonitoredDomainRepository
    checkers: dict[str, ExpirationChecker]
    retry_policy: RetryPolicy
    publisher: EventPublisher
    clock: TimeProvider
    sleeper: Sleeper = asyncio.sleep

    async def execute(self, name: DomainName) -> CheckResult:
        """Run the check loop and return the final ``CheckResult``."""
        domain = await self.repo.get(name)
        if domain is None:
            raise DomainNotMonitoredError(f"no monitored domain {name.value!r}")
        try:
            checker = self.checkers[domain.checker_id]
        except KeyError as exc:
            raise CheckerNotRegisteredError(
                f"no checker registered for id {domain.checker_id!r}"
            ) from exc

        last_result: CheckResult | None = None
        last_reason: str = ""
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                result = await checker.check(name)
            except TransientCheckError as exc:
                last_reason = str(exc) or "transient_error"
                if attempt < self.retry_policy.max_attempts:
                    await self._wait(attempt)
                    continue
                # Exhausted retries
                await self._emit_failed(
                    domain.name, checker.id, reason=last_reason, transient=True
                )
                raise
            except PermanentCheckError as exc:
                await self._emit_failed(
                    domain.name,
                    checker.id,
                    reason=str(exc) or "permanent_error",
                    transient=False,
                )
                raise

            last_result = result
            if result.outcome is CheckOutcome.TRANSIENT_ERROR:
                last_reason = result.error or "transient_error"
                if attempt < self.retry_policy.max_attempts:
                    await self._wait(attempt)
                    continue
                # Exhausted retries — surface the result via DomainCheckFailed.
                await self._emit_failed(
                    domain.name, checker.id, reason=last_reason, transient=True
                )
                return result
            if result.outcome is CheckOutcome.PERMANENT_ERROR:
                await self._emit_failed(
                    domain.name,
                    checker.id,
                    reason=result.error or "permanent_error",
                    transient=False,
                )
                return result

            # OK
            new_domain = domain.with_check_result(result, at=self.clock.now())
            await self.repo.update(new_domain)
            await self.publisher.publish(
                DomainCheckCompleted(occurred_at=self.clock.now(), result=result)
            )
            return result

        # Should be unreachable: the loop above either returns or re-raises
        # before exhausting attempts. Defensive guard — surfaces a clear
        # error if the policy invariants change.
        if last_result is None:
            raise RuntimeError(
                "check_domain: retry loop exited without producing a result"
            )
        return last_result

    async def _wait(self, attempt: int) -> None:
        delay = self.retry_policy.delay_for(attempt).seconds
        await self.sleeper(delay)

    async def _emit_failed(
        self,
        name: DomainName,
        source: str,
        *,
        reason: str,
        transient: bool,
    ) -> None:
        await self.publisher.publish(
            DomainCheckFailed(
                occurred_at=self.clock.now(),
                domain=name,
                source=source,
                reason=reason,
                transient=transient,
            )
        )


__all__ = [
    "CheckDomainUseCase",
    "CheckerNotRegisteredError",
    "DomainNotMonitoredError",
]
