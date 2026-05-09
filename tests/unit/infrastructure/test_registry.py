"""Registry: register/get/all, duplicate, lookup-miss."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pytest

from domain_watcher.infrastructure.registry import (
    PluginConflictError,
    PluginNotFoundError,
    Registry,
)


@dataclass
class _Plugin:
    id: ClassVar[str] = "x"


@dataclass
class _PluginA:
    id: ClassVar[str] = "a"


@dataclass
class _PluginB:
    id: ClassVar[str] = "b"


def test_register_get_all() -> None:
    r: Registry = Registry()
    a = _PluginA()
    b = _PluginB()
    r.register(a)
    r.register(b)
    assert r.get("a") is a
    assert r.get("b") is b
    assert {p.id for p in r.all()} == {"a", "b"}
    assert "a" in r
    assert len(r) == 2


def test_duplicate_id_conflicts() -> None:
    r: Registry = Registry()
    r.register(_Plugin())
    with pytest.raises(PluginConflictError):
        r.register(_Plugin())


def test_missing_id_raises() -> None:
    r: Registry = Registry()
    with pytest.raises(PluginNotFoundError):
        r.get("nope")
