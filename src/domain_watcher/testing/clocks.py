"""Stable re-export of :class:`FixedClock` for plugin authors.

The implementation lives in ``core/`` because tests are first-class
citizens: a plugin author MUST be able to drive their plugin against
deterministic time without depending on infrastructure modules.
"""

from __future__ import annotations

from domain_watcher.core.shared.time_provider import FixedClock, SystemClock, TimeProvider

__all__ = ["FixedClock", "SystemClock", "TimeProvider"]
