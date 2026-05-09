"""Concrete ``ExpirationChecker`` adapters.

Public surface:

- ``RdapChecker`` (id ``"rdap"``)
- ``WhoisCheckerWithParser`` (id ``"whois"``)
- ``ScriptChecker`` (id ``"script"``)

``_WhoisFetcher`` is intentionally *not* re-exported: the raw fetcher is
composition-only. Importers wire it through ``WhoisCheckerWithParser``.
"""

from __future__ import annotations

from domain_watcher.infrastructure.checkers._whois_with_parser import (
    WhoisCheckerWithParser,
)
from domain_watcher.infrastructure.checkers.rdap import RdapChecker
from domain_watcher.infrastructure.checkers.script import ScriptChecker

__all__ = [
    "RdapChecker",
    "ScriptChecker",
    "WhoisCheckerWithParser",
]
