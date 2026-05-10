"""SQL-backed ``LearnedRulesRepository``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from domain_watcher.core.parsing.value_objects import (
    DateFormat,
    LearnedRule,
    ParseRule,
    RegexPattern,
)
from domain_watcher.core.shared.value_objects import DomainName
from domain_watcher.infrastructure.persistence.sql.orm import LearnedRuleRow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    pass


def _row_to_learned(row: LearnedRuleRow) -> LearnedRule:
    return LearnedRule(
        id=row.id,
        tld=row.tld,
        expires_regex=RegexPattern(row.expires_regex),
        date_format=DateFormat(row.date_format),
        timezone=row.timezone,
        strptime_format=row.strptime_format,
        auto_learned=row.auto_learned,
        disabled=row.disabled,
        suggester_id=row.suggester_id,
        pipeline_version=row.pipeline_version,
        sample_whois_sha256=row.sample_whois_sha256,
        sample_domain=DomainName(row.sample_domain),
        created_at=(
            row.created_at
            if row.created_at.tzinfo
            else row.created_at.replace(tzinfo=UTC)
        ),
        last_revalidated_at=(
            row.last_revalidated_at.replace(tzinfo=UTC)
            if row.last_revalidated_at is not None
            and row.last_revalidated_at.tzinfo is None
            else row.last_revalidated_at
        ),
        revalidation_count=row.revalidation_count,
    )


def _row_to_parse(row: LearnedRuleRow) -> ParseRule:
    return ParseRule(
        tld=row.tld,
        expires_regex=RegexPattern(row.expires_regex),
        date_format=DateFormat(row.date_format),
        timezone=row.timezone,
        strptime_format=row.strptime_format,
    )


class SqlLearnedRulesRepo:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_tld(self, tld: str) -> Sequence[ParseRule]:
        stmt = select(LearnedRuleRow).where(
            LearnedRuleRow.tld == tld,
            LearnedRuleRow.disabled == False,  # noqa: E712
        )
        result = await self._session.execute(stmt)
        return tuple(_row_to_parse(r) for r in result.scalars().all())

    async def add(
        self,
        rule: ParseRule,
        *,
        sample_sha256: str,
        sample_domain: DomainName,
        suggester_id: str,
        pipeline_version: int,
    ) -> int:
        row = LearnedRuleRow(
            tld=rule.tld,
            expires_regex=rule.expires_regex.raw,
            date_format=rule.date_format.value,
            strptime_format=rule.strptime_format,
            timezone=rule.timezone,
            auto_learned=True,
            disabled=False,
            suggester_id=suggester_id,
            pipeline_version=pipeline_version,
            sample_whois_sha256=sample_sha256,
            sample_domain=sample_domain.value,
            created_at=datetime.now(tz=UTC),
            last_revalidated_at=None,
            revalidation_count=0,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(
                f"duplicate learned rule for tld={rule.tld!r} "
                f"regex={rule.expires_regex.raw!r}"
            ) from exc
        return int(row.id)

    async def disable(self, rule_id: int, reason: str) -> None:
        existing = await self._session.get(LearnedRuleRow, rule_id)
        if existing is None:
            raise KeyError(f"no learned rule {rule_id}")
        existing.disabled = True
        await self._session.flush()
        _ = reason

    async def list_all(
        self,
        *,
        include_disabled: bool = False,
    ) -> Sequence[LearnedRule]:
        stmt = select(LearnedRuleRow)
        if not include_disabled:
            stmt = stmt.where(LearnedRuleRow.disabled == False)  # noqa: E712
        result = await self._session.execute(stmt)
        return tuple(_row_to_learned(r) for r in result.scalars().all())

    async def mark_revalidated(self, rule_id: int, at: datetime) -> None:
        stmt = (
            update(LearnedRuleRow)
            .where(LearnedRuleRow.id == rule_id)
            .values(
                last_revalidated_at=at,
                revalidation_count=LearnedRuleRow.revalidation_count + 1,
            )
        )
        result = await self._session.execute(stmt)
        # SQLAlchemy's CursorResult exposes ``rowcount``; ``Result`` parent
        # class does not advertise it. Cast here to keep the typed surface
        # narrow without polluting the call site.
        rowcount = getattr(result, "rowcount", 0)
        if rowcount == 0:
            raise KeyError(f"no learned rule {rule_id}")
        await self._session.flush()


__all__ = ["SqlLearnedRulesRepo"]
