"""Entry-point plugin discovery (ADR 0004 §5.2, §9).

Public surface re-exported via :mod:`domain_watcher.infrastructure.plugins.discovery`:

* :data:`PLUGIN_PROTOCOL_VERSION` — host's plugin protocol version.
* :func:`discover` — load entry points for a group, applying enabled / disabled filters.
* :class:`PluginGroup` — string enum of the four supported groups.
* :class:`PluginLoadError` — raised when a plugin fails to import or violates the
  protocol-version contract.
"""

from __future__ import annotations

from domain_watcher.infrastructure.plugins.discovery import (
    PLUGIN_PROTOCOL_VERSION,
    PluginGroup,
    PluginLoadError,
    discover,
)

__all__ = [
    "PLUGIN_PROTOCOL_VERSION",
    "PluginGroup",
    "PluginLoadError",
    "discover",
]
