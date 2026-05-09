"""Pydantic ``Annotated`` wrapper for the core ``Duration`` value object.

Lets YAML strings like ``"30d"`` and bare integer-second values land as
``Duration`` instances after model validation. Keeps Pydantic out of
``core/`` — the wrapper lives in ``infrastructure/config/``.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer

from domain_watcher.core.shared.value_objects import Duration


def _coerce(value: Any) -> Duration:
    """Coerce ``"30d"``, ``Duration`` instances, or bare ints into ``Duration``."""
    if isinstance(value, Duration):
        return value
    if isinstance(value, bool):
        # bool is an int — reject explicitly; otherwise ``True`` becomes 1s.
        raise ValueError(f"Duration value must be str|int|Duration, got bool: {value!r}")
    if isinstance(value, int):
        return Duration.from_seconds(value)
    if isinstance(value, str):
        return Duration.parse(value)
    raise ValueError(f"Duration value must be str|int|Duration, got {type(value).__name__}")


def _serialize(value: Duration) -> str:
    return str(value)


DurationField = Annotated[
    Duration,
    BeforeValidator(_coerce),
    PlainSerializer(_serialize, return_type=str, when_used="json"),
]
"""Pydantic field type backed by the core ``Duration`` value object.

Accepts ``"30d"`` / ``"12h"`` / ``"5m"`` / ``"60s"`` strings, bare integer
seconds, or pre-built ``Duration`` instances. Serialises back to the
canonical compact form (``"30d"``) for JSON dumps.
"""


__all__ = ["DurationField"]
