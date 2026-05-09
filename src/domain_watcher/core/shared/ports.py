"""Cross-context ports living in ``core/shared``.

Currently hosts the ``RateLimiter`` Protocol consumed by ``ParsingService``
(per-host and per-TLD learn-call rate limits — ADR 0006 §7). Adapters live
in ``infrastructure/`` and may use any token-bucket implementation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RateLimiter(Protocol):
    """Generic rate-limit gate keyed by an opaque string.

    ``acquire`` returns ``True`` when a token was obtained (the caller may
    proceed) and ``False`` otherwise (the caller must back off). Adapters
    are responsible for refill semantics; ``core/`` only encodes the gate
    contract.
    """

    async def acquire(self, key: str) -> bool: ...


__all__ = ["RateLimiter"]
