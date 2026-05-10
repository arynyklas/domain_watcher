"""Fake plugin used by ``tests/integration/plugins/test_discovery_real.py``."""

from __future__ import annotations

PROTOCOL_VERSION = 1
"""Must match ``domain_watcher.infrastructure.plugins.PLUGIN_PROTOCOL_VERSION``.

The integration test asserts the host loads this plugin only when the
declared version matches.
"""
