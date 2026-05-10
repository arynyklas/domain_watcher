"""Unit tests for entry-point plugin discovery (Task 11.1).

The real :func:`importlib.metadata.entry_points` is bypassed by patching
:func:`domain_watcher.infrastructure.plugins.discovery._entry_points_for`.
The integration test in ``tests/integration/plugins`` exercises the real
loader against a temporarily-installed fake package.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pytest

from domain_watcher.infrastructure.plugins import discovery
from domain_watcher.infrastructure.plugins.discovery import (
    PLUGIN_PROTOCOL_VERSION,
    PluginGroup,
    PluginLoadError,
    discover,
)

# --- Fake EntryPoint plumbing -----------------------------------------------


@dataclass
class _FakeMetadata:
    name: str

    def get(self, key: str) -> str | None:
        return self.name if key == "Name" else None


@dataclass
class _FakeDist:
    metadata: _FakeMetadata


@dataclass
class _FakeEntryPoint:
    name: str
    value: object
    dist_name: str | None = None
    raises: BaseException | None = None

    @property
    def dist(self) -> _FakeDist | None:
        return (
            _FakeDist(metadata=_FakeMetadata(self.dist_name))
            if self.dist_name
            else None
        )

    def load(self) -> object:
        if self.raises is not None:
            raise self.raises
        return self.value


def _stub_entry_points(
    monkeypatch: pytest.MonkeyPatch, mapping: dict[str, Iterable[_FakeEntryPoint]]
) -> None:
    def _fake(group: str) -> list[_FakeEntryPoint]:
        return list(mapping.get(group, ()))

    monkeypatch.setattr(discovery, "_entry_points_for", _fake)


# --- Sample plugin classes used in tests ------------------------------------


class _CheckerA:
    id = "alpha"


class _CheckerB:
    id = "beta"


class _CheckerC:
    id = "gamma"


# --- Tests ------------------------------------------------------------------


def test_discover_loads_all_when_no_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_entry_points(
        monkeypatch,
        {
            PluginGroup.CHECKERS.value: [
                _FakeEntryPoint(name="alpha", value=_CheckerA, dist_name="pkg-a"),
                _FakeEntryPoint(name="beta", value=_CheckerB, dist_name="pkg-b"),
            ],
        },
    )

    out = discover(PluginGroup.CHECKERS)

    assert out == {"alpha": _CheckerA, "beta": _CheckerB}


def test_discover_accepts_str_group_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_entry_points(
        monkeypatch,
        {
            "domain_watcher.checkers": [
                _FakeEntryPoint(name="alpha", value=_CheckerA, dist_name="pkg-a"),
            ],
        },
    )

    out = discover("domain_watcher.checkers")

    assert out == {"alpha": _CheckerA}


def test_discover_allowlist_beats_denylist(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``enabled`` is non-empty it wins; ``disabled`` is ignored."""

    _stub_entry_points(
        monkeypatch,
        {
            PluginGroup.CHECKERS.value: [
                _FakeEntryPoint(name="alpha", value=_CheckerA, dist_name="pkg-a"),
                _FakeEntryPoint(name="beta", value=_CheckerB, dist_name="pkg-b"),
                _FakeEntryPoint(name="gamma", value=_CheckerC, dist_name="pkg-c"),
            ],
        },
    )

    # ``disabled`` would have removed alpha, but ``enabled`` overrides it.
    out = discover(PluginGroup.CHECKERS, enabled=["alpha", "beta"], disabled=["alpha"])

    assert set(out) == {"alpha", "beta"}


def test_discover_denylist_when_no_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_entry_points(
        monkeypatch,
        {
            PluginGroup.CHECKERS.value: [
                _FakeEntryPoint(name="alpha", value=_CheckerA, dist_name="pkg-a"),
                _FakeEntryPoint(name="beta", value=_CheckerB, dist_name="pkg-b"),
            ],
        },
    )

    out = discover(PluginGroup.CHECKERS, disabled=["beta"])

    assert set(out) == {"alpha"}


def test_discover_import_failure_names_package_and_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boom = ImportError("missing dep 'requests'")
    _stub_entry_points(
        monkeypatch,
        {
            PluginGroup.NOTIFIERS.value: [
                _FakeEntryPoint(
                    name="webhook", value=None, dist_name="pkg-x", raises=boom
                ),
            ],
        },
    )

    with pytest.raises(PluginLoadError) as excinfo:
        discover(PluginGroup.NOTIFIERS)

    msg = str(excinfo.value)
    assert "pkg-x" in msg
    assert "webhook" in msg
    assert "missing dep" in msg
    assert excinfo.value.group == PluginGroup.NOTIFIERS.value
    assert excinfo.value.name == "webhook"


def test_discover_protocol_version_mismatch_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_version = PLUGIN_PROTOCOL_VERSION + 1
    _stub_entry_points(
        monkeypatch,
        {
            "domain_watcher.metadata": [
                _FakeEntryPoint(
                    name="protocol_version", value=bad_version, dist_name="pkg-a"
                ),
            ],
            PluginGroup.CHECKERS.value: [
                _FakeEntryPoint(name="alpha", value=_CheckerA, dist_name="pkg-a"),
            ],
        },
    )

    with pytest.raises(PluginLoadError) as excinfo:
        discover(PluginGroup.CHECKERS)

    msg = str(excinfo.value)
    assert "protocol_version" in msg
    assert str(bad_version) in msg
    assert str(PLUGIN_PROTOCOL_VERSION) in msg


def test_discover_protocol_version_match_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_entry_points(
        monkeypatch,
        {
            "domain_watcher.metadata": [
                _FakeEntryPoint(
                    name="protocol_version",
                    value=PLUGIN_PROTOCOL_VERSION,
                    dist_name="pkg-a",
                ),
            ],
            PluginGroup.CHECKERS.value: [
                _FakeEntryPoint(name="alpha", value=_CheckerA, dist_name="pkg-a"),
            ],
        },
    )

    out = discover(PluginGroup.CHECKERS)

    assert out == {"alpha": _CheckerA}


def test_discover_protocol_version_must_be_int(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_entry_points(
        monkeypatch,
        {
            "domain_watcher.metadata": [
                _FakeEntryPoint(name="protocol_version", value="1", dist_name="pkg-a"),
            ],
            PluginGroup.CHECKERS.value: [
                _FakeEntryPoint(name="alpha", value=_CheckerA, dist_name="pkg-a"),
            ],
        },
    )

    with pytest.raises(PluginLoadError, match="protocol_version must be int"):
        discover(PluginGroup.CHECKERS)


def test_discover_rejects_non_class_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0004 §5.2 requires entry points to resolve to a class."""

    not_a_class: Any = lambda: None  # noqa: E731 — lambda is the point
    _stub_entry_points(
        monkeypatch,
        {
            PluginGroup.CHECKERS.value: [
                _FakeEntryPoint(name="alpha", value=not_a_class, dist_name="pkg-a"),
            ],
        },
    )

    with pytest.raises(PluginLoadError, match="did not resolve to a class"):
        discover(PluginGroup.CHECKERS)


def test_discover_skipped_filter_does_not_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin filtered out by the allowlist MUST NOT be imported.

    Important for plugins that have heavy or failing imports — if they're
    disabled, they should not even attempt to load.
    """

    boom = ImportError("would have failed")
    _stub_entry_points(
        monkeypatch,
        {
            PluginGroup.CHECKERS.value: [
                _FakeEntryPoint(name="alpha", value=_CheckerA, dist_name="pkg-a"),
                _FakeEntryPoint(
                    name="boom", value=None, dist_name="pkg-b", raises=boom
                ),
            ],
        },
    )

    out = discover(PluginGroup.CHECKERS, enabled=["alpha"])

    assert out == {"alpha": _CheckerA}


def test_plugin_group_str_value_matches_adr() -> None:
    assert PluginGroup.CHECKERS.value == "domain_watcher.checkers"
    assert PluginGroup.NOTIFIERS.value == "domain_watcher.notifiers"
    assert PluginGroup.PARSERS.value == "domain_watcher.parsers"
    assert PluginGroup.RULE_SUGGESTERS.value == "domain_watcher.rule_suggesters"
