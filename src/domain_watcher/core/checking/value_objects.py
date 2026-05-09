"""Result + outcome of a single expiration check (ADR 0002 §3)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from domain_watcher.core.shared.value_objects import DomainName


class CheckOutcome(StrEnum):
    """Coarse result classification.

    A checker either knows the answer (``OK``), needs to retry (``TRANSIENT``),
    or has authoritative bad news (``PERMANENT``). It MUST NOT fabricate an
    expiration date — the parser, not the checker, is the source of truth.
    """

    OK = "ok"
    TRANSIENT_ERROR = "transient_error"
    PERMANENT_ERROR = "permanent_error"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """What an ``ExpirationChecker`` returns.

    Invariants enforced in ``__post_init__``:
      - ``outcome == OK`` ⇔ ``expires_at`` is not None.
      - ``outcome != OK`` ⇒ ``error`` is set (human-readable).
      - ``expires_at`` (when set) is tz-aware.
    """

    domain: DomainName
    outcome: CheckOutcome
    expires_at: datetime | None
    source: str
    raw: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        ok = self.outcome is CheckOutcome.OK
        has_date = self.expires_at is not None
        if ok != has_date:
            raise ValueError(
                "CheckResult invariant violated: outcome == OK ⇔ expires_at is not None"
            )
        if not ok and not self.error:
            raise ValueError("CheckResult error message required for non-OK outcomes")
        if self.expires_at is not None:
            tzinfo = self.expires_at.tzinfo
            if tzinfo is None or tzinfo.utcoffset(self.expires_at) is None:
                raise ValueError("CheckResult.expires_at must be tz-aware UTC")


__all__ = ["CheckOutcome", "CheckResult"]
