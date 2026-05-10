"""Typed plugin registry.

Used by composition code to map ``id`` strings (from YAML config) to
concrete adapters (checkers, notifiers, suggesters). The registry only
enforces uniqueness and lookup; no introspection magic.

ADR 0004 §4.1: every adapter ships an ``id: ClassVar[str]``. The registry
indexes by that field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Iterator


class _HasId(Protocol):
    id: str


T = TypeVar("T", bound=_HasId)


class PluginConflictError(ValueError):
    """Two plugins claimed the same ``id``."""


class PluginNotFoundError(KeyError):
    """No plugin registered under the requested ``id``."""


class Registry[T: _HasId]:
    """Insertion-ordered ``str → T`` store with conflict + lookup-miss errors."""

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: dict[str, T] = {}

    def register(self, obj: T) -> None:
        plugin_id = obj.id
        if not plugin_id:
            raise ValueError(f"plugin missing id: {obj!r}")
        if plugin_id in self._items:
            raise PluginConflictError(
                f"plugin id {plugin_id!r} already registered "
                f"({self._items[plugin_id]!r})"
            )
        self._items[plugin_id] = obj

    def get(self, plugin_id: str) -> T:
        try:
            return self._items[plugin_id]
        except KeyError as exc:
            raise PluginNotFoundError(
                f"no plugin registered for id {plugin_id!r}; "
                f"known: {sorted(self._items)}"
            ) from exc

    def all(self) -> tuple[T, ...]:
        return tuple(self._items.values())

    def __contains__(self, plugin_id: object) -> bool:
        return plugin_id in self._items

    def __iter__(self) -> Iterator[T]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)


__all__ = [
    "PluginConflictError",
    "PluginNotFoundError",
    "Registry",
]
