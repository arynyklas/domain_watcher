"""initial schema: monitored_domains, learned_rules, alert_idempotency.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-09 00:00:00
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitored_domains",
        sa.Column("name", sa.String(length=253), nullable=False),
        sa.Column("cron", sa.String(length=64), nullable=False),
        sa.Column("checker_id", sa.String(length=64), nullable=False),
        sa.Column("notify_thresholds_secs", sa.Text(), nullable=False),
        sa.Column("channels", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_outcome", sa.String(length=32), nullable=True),
        sa.Column("last_check_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("name", name="pk_monitored_domains"),
    )

    op.create_table(
        "learned_rules",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("tld", sa.String(length=253), nullable=False),
        sa.Column("expires_regex", sa.Text(), nullable=False),
        sa.Column("date_format", sa.String(length=32), nullable=False),
        sa.Column("strptime_format", sa.Text(), nullable=True),
        sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default="UTC"
        ),
        sa.Column(
            "auto_learned", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("suggester_id", sa.String(length=128), nullable=False),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.Column("sample_whois_sha256", sa.String(length=64), nullable=False),
        sa.Column("sample_domain", sa.String(length=253), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_revalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revalidation_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_learned_rules"),
        sa.UniqueConstraint("tld", "expires_regex", name="uq_learned_rules_tld_regex"),
    )
    op.create_index(
        "ix_learned_rules_tld_active",
        "learned_rules",
        ["tld"],
        unique=False,
        postgresql_where=sa.text("disabled = false"),
    )

    op.create_table(
        "alert_idempotency",
        sa.Column("domain_name", sa.String(length=253), nullable=False),
        sa.Column("threshold_secs", sa.BigInteger(), nullable=False),
        sa.Column("cycle_id", sa.String(length=16), nullable=False),
        sa.Column("channel_id", sa.String(length=128), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "domain_name",
            "threshold_secs",
            "cycle_id",
            "channel_id",
            name="pk_alert_idempotency",
        ),
    )


def downgrade() -> None:
    op.drop_table("alert_idempotency")
    op.drop_index("ix_learned_rules_tld_active", table_name="learned_rules")
    op.drop_table("learned_rules")
    op.drop_table("monitored_domains")
