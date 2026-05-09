"""SQLAlchemy 2 ORM table definitions.

Mirrors ADR 0006 §9 (``learned_rules``) plus monitored-domain and
``alert_idempotency`` tables. Naming convention is fixed so generated
constraint names are deterministic across migrations.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class MonitoredDomainRow(Base):
    """One row per monitored FQDN."""

    __tablename__ = "monitored_domains"

    name: Mapped[str] = mapped_column(String(253), primary_key=True)
    cron: Mapped[str] = mapped_column(String(64), nullable=False)
    checker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    notify_thresholds_secs: Mapped[str] = mapped_column(Text, nullable=False)
    """Comma-separated descending integer seconds. e.g. ``"2592000,604800,86400"``."""
    channels: Mapped[str] = mapped_column(Text, nullable=False)
    """Comma-separated channel ids."""
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_check_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LearnedRuleRow(Base):
    """ADR 0006 §9 — runtime-learned WHOIS rules."""

    __tablename__ = "learned_rules"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    tld: Mapped[str] = mapped_column(String(253), nullable=False)
    expires_regex: Mapped[str] = mapped_column(Text, nullable=False)
    date_format: Mapped[str] = mapped_column(String(32), nullable=False)
    strptime_format: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    auto_learned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suggester_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pipeline_version: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_whois_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    last_revalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revalidation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("tld", "expires_regex", name="uq_learned_rules_tld_regex"),
        Index("ix_learned_rules_tld_active", "tld", postgresql_where="disabled = false"),
    )


class AlertIdempotencyRow(Base):
    """4-tuple key. PK absorbs the deduplication contract directly."""

    __tablename__ = "alert_idempotency"

    domain_name: Mapped[str] = mapped_column(String(253), primary_key=True)
    threshold_secs: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


_ = relationship  # keep import if future relationships land
_ = ForeignKey


__all__ = [
    "NAMING_CONVENTION",
    "AlertIdempotencyRow",
    "Base",
    "LearnedRuleRow",
    "MonitoredDomainRow",
]
