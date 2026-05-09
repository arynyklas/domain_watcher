"""Ports (Protocols) for the checking bounded context (ADR 0002 §3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from domain_watcher.core.checking.value_objects import CheckResult
    from domain_watcher.core.shared.value_objects import DomainName


@runtime_checkable
class ExpirationChecker(Protocol):
    """Asks a single source for ``expires_at`` of a domain.

    The contract: never fabricate a date. If the checker doesn't know,
    return ``CheckResult`` with ``TRANSIENT_ERROR`` or ``PERMANENT_ERROR``
    and a human-readable ``error``. Callers compose retries via
    ``RetryPolicy``.
    """

    id: ClassVar[str]

    async def check(self, domain: DomainName) -> CheckResult: ...


__all__ = ["ExpirationChecker"]
