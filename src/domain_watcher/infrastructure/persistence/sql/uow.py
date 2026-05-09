"""SQLAlchemy 2 ``async_sessionmaker``-backed UnitOfWork."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from types import TracebackType

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SqlUnitOfWork:
    """Async UoW yielding a per-context ``AsyncSession``.

    Usage::

        uow = SqlUnitOfWork(session_factory)
        async with uow as session:
            await session.execute(...)
            await uow.commit()

    Exiting without ``commit`` rolls back. Exception propagation also rolls
    back.
    """

    __slots__ = ("_committed", "_factory", "_session")

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory
        self._session: AsyncSession | None = None
        self._committed = False

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("SqlUnitOfWork session accessed outside context")
        return self._session

    async def __aenter__(self) -> Self:
        self._session = self._factory()
        self._committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        sess = self._session
        if sess is None:
            return
        try:
            if exc_type is not None or not self._committed:
                await sess.rollback()
        finally:
            await sess.close()
            self._session = None
            self._committed = False

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("SqlUnitOfWork.commit outside context")
        await self._session.commit()
        self._committed = True

    async def rollback(self) -> None:
        if self._session is None:
            raise RuntimeError("SqlUnitOfWork.rollback outside context")
        await self._session.rollback()
        self._committed = False


__all__ = ["SqlUnitOfWork"]
