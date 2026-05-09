"""Unit-of-work port and a no-op memory implementation.

The SQL adapter (Phase 3) supplies a real impl wrapping
``async_sessionmaker``. Application use cases ``async with`` the port to
delineate transactional boundaries; for in-memory tests the no-op variant
keeps the call sites identical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

if TYPE_CHECKING:
    from types import TracebackType


@runtime_checkable
class UnitOfWork(Protocol):
    """Async context-managed transactional boundary.

    Entering the context begins a unit; ``commit`` persists, ``rollback``
    discards. Exiting without an explicit ``commit`` rolls back — adapters
    are responsible for enforcing this in ``__aexit__``.
    """

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class MemoryUnitOfWork:
    """No-op UoW for in-memory tests — all repositories share global state.

    Enter/exit are cheap. ``commit``/``rollback`` are recorded so tests can
    assert on the call history when the use case under test claims to
    finalize a transaction.
    """

    __slots__ = ("_in_context", "commits", "rollbacks")

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self._in_context = False

    async def __aenter__(self) -> MemoryUnitOfWork:
        self._in_context = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()
        self._in_context = False

    async def commit(self) -> None:
        if not self._in_context:
            raise RuntimeError("MemoryUnitOfWork.commit outside context")
        self.commits += 1

    async def rollback(self) -> None:
        if not self._in_context:
            raise RuntimeError("MemoryUnitOfWork.rollback outside context")
        self.rollbacks += 1


__all__ = ["MemoryUnitOfWork", "UnitOfWork"]
