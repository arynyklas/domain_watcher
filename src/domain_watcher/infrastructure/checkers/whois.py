"""WHOIS fetcher (``_WhoisFetcher``) — sync ``python-whois`` wrapped in ``to_thread``.

This adapter is internal: composition wires
``WhoisCheckerWithParser`` (id ``"whois"``) which combines ``_WhoisFetcher``
with a ``WhoisParser`` (Phase 5). The bare fetcher never produces an
``expires_at`` itself — parsing is the parser's job. Honouring this
boundary keeps the fetcher's contract simple: text in, text out.
"""

from __future__ import annotations

import asyncio
import re
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import whois  # type: ignore[import-untyped]

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult

if TYPE_CHECKING:
    from domain_watcher.core.shared.value_objects import DomainName

# Common "no such domain" markers across registries. Conservative — unknown
# replies fall through to TRANSIENT and let the use case retry.
_NO_MATCH_PATTERNS = (
    re.compile(r"^\s*no\s+match", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*not\s+found", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*no\s+entries\s+found", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*domain\s+not\s+found", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*no\s+data\s+found", re.IGNORECASE | re.MULTILINE),
)


def _looks_like_no_match(raw: str) -> bool:
    return any(p.search(raw) for p in _NO_MATCH_PATTERNS)


def _extract_text(record: Any) -> str:
    """Pull the raw WHOIS body out of whatever shape ``python-whois`` returned."""
    text = getattr(record, "text", None)
    if isinstance(text, str) and text:
        return text
    raw = getattr(record, "raw", None)
    if isinstance(raw, list) and raw:
        return "\n".join(str(piece) for piece in raw)
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(record, str):
        return record
    if isinstance(record, dict):
        # python-whois fallback dict
        text = record.get("text") or record.get("raw")
        if isinstance(text, list):
            return "\n".join(str(piece) for piece in text)
        if isinstance(text, str):
            return text
    return ""


@dataclass(slots=True)
class _WhoisFetcher:
    """Internal raw WHOIS fetcher.

    NOT registered under the public adapters surface — composition only.
    """

    id: ClassVar[str] = "_whois_fetcher"

    timeout: float = 30.0

    async def fetch(self, domain: DomainName) -> CheckResult:
        try:
            record = await asyncio.wait_for(
                asyncio.to_thread(whois.whois, domain.value),
                timeout=self.timeout,
            )
        except TimeoutError:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.TRANSIENT_ERROR,
                expires_at=None,
                source=self.id,
                error="whois timeout",
            )
        except (socket.gaierror, ConnectionError, OSError) as exc:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.TRANSIENT_ERROR,
                expires_at=None,
                source=self.id,
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            # Treat unknown exceptions as transient: the registry may be flaky.
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.TRANSIENT_ERROR,
                expires_at=None,
                source=self.id,
                error=f"{type(exc).__name__}: {exc}",
            )

        text = _extract_text(record)
        if not text:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.TRANSIENT_ERROR,
                expires_at=None,
                source=self.id,
                error="whois returned empty payload",
            )
        if _looks_like_no_match(text):
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                raw=text,
                error="no match",
            )
        # OK with raw text; the WhoisCheckerWithParser composite resolves
        # ``expires_at`` via the parser. Returning OK here is wrong because
        # ``CheckResult`` requires ``expires_at`` set on OK — return TRANSIENT
        # with raw embedded so the composite owns the success path.
        # We use a sentinel outcome below in the composite; here we only
        # emit a non-OK result whose ``raw`` carries the text.
        return CheckResult(
            domain=domain,
            outcome=CheckOutcome.TRANSIENT_ERROR,  # stays in retry budget if composite doesn't run
            expires_at=None,
            source=self.id,
            raw=text,
            error="raw whois fetched; composite parses",
        )


__all__ = ["_WhoisFetcher"]
