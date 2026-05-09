"""MemoryUnitOfWork: enter/exit, commit/rollback, exception path."""

from __future__ import annotations

import pytest

from domain_watcher.application.unit_of_work import MemoryUnitOfWork, UnitOfWork


async def test_commit_inside_context() -> None:
    uow = MemoryUnitOfWork()
    async with uow:
        await uow.commit()
    assert uow.commits == 1
    assert uow.rollbacks == 0


async def test_commit_outside_context_raises() -> None:
    uow = MemoryUnitOfWork()
    with pytest.raises(RuntimeError):
        await uow.commit()


async def test_exception_triggers_rollback() -> None:
    uow = MemoryUnitOfWork()
    with pytest.raises(ValueError):
        async with uow:
            raise ValueError("boom")
    assert uow.rollbacks == 1


async def test_protocol_isinstance() -> None:
    uow = MemoryUnitOfWork()
    assert isinstance(uow, UnitOfWork)
