"""Entry-point discovery for plugin groups.

ADR 0004 defines four plugin groups. A plugin package declares one or more
entry points under those groups (see ADR 0004 §5.2) and optionally a
``domain_watcher.metadata.protocol_version`` entry point that resolves to an
``int``. Discovery loads each entry point, filters by the
``runtime.plugins.enabled`` / ``disabled`` lists, and refuses to load any
plugin whose declared ``protocol_version`` does not match the host's
``PLUGIN_PROTOCOL_VERSION`` (ADR 0004 §9).

Errors during loading are surfaced as :class:`PluginLoadError`. Discovery
never silently drops a plugin: a missing dependency, a bad entry point,
or a protocol mismatch is fatal for that plugin and the error names both
the package and the entry-point name. The composition root is responsible
for deciding whether a single bad plugin should fail startup or be skipped
with a warning — discovery only reports the truth.
"""

from __future__ import annotations

import enum
import logging
from importlib.metadata import EntryPoint, EntryPoints, entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

PLUGIN_PROTOCOL_VERSION = 1
"""Host's plugin protocol version.

Bumped when the plugin contract changes in a backwards-incompatible way.
Plugins that declare a different value are refused.
"""

_METADATA_GROUP = "domain_watcher.metadata"
_PROTOCOL_VERSION_NAME = "protocol_version"

_log = logging.getLogger(__name__)


class PluginGroup(enum.StrEnum):
    """The four supported entry-point groups (ADR 0004 §3)."""

    CHECKERS = "domain_watcher.checkers"
    NOTIFIERS = "domain_watcher.notifiers"
    PARSERS = "domain_watcher.parsers"
    RULE_SUGGESTERS = "domain_watcher.rule_suggesters"


class PluginLoadError(RuntimeError):
    """Raised when an entry point cannot be loaded or violates the protocol contract.

    The message names both the distribution package and the entry-point name
    so an operator can find the offending plugin without having to enumerate
    installed packages.
    """

    def __init__(self, *, group: str, name: str, dist: str | None, reason: str) -> None:
        package = dist or "<unknown-package>"
        super().__init__(f"plugin {package}:{group}:{name} failed to load: {reason}")
        self.group = group
        self.name = name
        self.dist = dist
        self.reason = reason


def _entry_points_for(group: str) -> EntryPoints:
    """Indirection so tests can stub the entry-point listing."""

    return entry_points(group=group)


def _dist_name(ep: EntryPoint) -> str | None:
    """Return the distribution name behind an entry point if available.

    ``EntryPoint.dist`` is populated when the entry points are obtained
    from :func:`importlib.metadata.entry_points`. Test stubs may leave it
    ``None`` — in that case the error message degrades gracefully.
    """

    dist = getattr(ep, "dist", None)
    if dist is None:
        return None
    metadata = getattr(dist, "metadata", None)
    if metadata is None:
        return None
    name = metadata.get("Name") if hasattr(metadata, "get") else None
    return str(name) if name else None


def _load_protocol_versions() -> dict[str, int]:
    """Map distribution name → declared protocol version.

    A plugin without a ``domain_watcher.metadata.protocol_version`` entry
    point is treated as targeting the host's current version (best-effort
    backwards compatibility, ADR 0004 §9). A plugin that explicitly
    declares a mismatching value is refused.
    """

    versions: dict[str, int] = {}
    for ep in _entry_points_for(_METADATA_GROUP):
        if ep.name != _PROTOCOL_VERSION_NAME:
            continue
        dist = _dist_name(ep) or ep.value
        try:
            value = ep.load()
        except Exception as exc:
            raise PluginLoadError(
                group=_METADATA_GROUP,
                name=ep.name,
                dist=dist,
                reason=f"failed to load protocol_version: {exc!r}",
            ) from exc
        if not isinstance(value, int):
            raise PluginLoadError(
                group=_METADATA_GROUP,
                name=ep.name,
                dist=dist,
                reason=f"protocol_version must be int, got {type(value).__name__}",
            )
        versions[dist] = value
    return versions


def _is_enabled(name: str, enabled: Iterable[str], disabled: Iterable[str]) -> bool:
    """Apply the (allowlist, denylist) filter described in ADR 0004 §5.3.

    Allowlist beats denylist: when ``enabled`` is non-empty, only ids in it
    are loaded. ``disabled`` is consulted only when ``enabled`` is empty.
    """

    enabled_set = set(enabled)
    disabled_set = set(disabled)
    if enabled_set:
        return name in enabled_set
    return name not in disabled_set


def discover(
    group: PluginGroup | str,
    *,
    enabled: Iterable[str] = (),
    disabled: Iterable[str] = (),
) -> dict[str, type]:
    """Load every entry point in ``group`` that passes the filter.

    Returns a mapping ``id -> class``. The ``id`` is the entry-point name
    (matching how the operator references plugins in YAML).

    Raises :class:`PluginLoadError` on first failure — discovery is
    fail-fast so a partially-loaded set never reaches the composition
    root. Filter mismatches are silent (the plugin is not loaded and not
    reported); import errors and protocol mismatches are loud.
    """

    group_name = group.value if isinstance(group, PluginGroup) else group
    versions = _load_protocol_versions()

    out: dict[str, type] = {}
    for ep in _entry_points_for(group_name):
        if not _is_enabled(ep.name, enabled, disabled):
            _log.debug("plugin %s:%s skipped by filter", group_name, ep.name)
            continue

        dist = _dist_name(ep)
        declared = versions.get(dist) if dist is not None else None
        if declared is not None and declared != PLUGIN_PROTOCOL_VERSION:
            raise PluginLoadError(
                group=group_name,
                name=ep.name,
                dist=dist,
                reason=(
                    f"declared protocol_version={declared} but host requires "
                    f"{PLUGIN_PROTOCOL_VERSION}"
                ),
            )

        try:
            obj = ep.load()
        except Exception as exc:
            raise PluginLoadError(
                group=group_name,
                name=ep.name,
                dist=dist,
                reason=f"import failed: {exc!r}",
            ) from exc

        if not isinstance(obj, type):
            raise PluginLoadError(
                group=group_name,
                name=ep.name,
                dist=dist,
                reason=f"entry point did not resolve to a class: {type(obj).__name__}",
            )
        out[ep.name] = obj

    return out


__all__ = [
    "PLUGIN_PROTOCOL_VERSION",
    "PluginGroup",
    "PluginLoadError",
    "discover",
]
