"""Scope operational Telegram proxy defaults to one organization.

Revision ID: 0013_telegram_proxy_workspace
Revises: 0012_staff_lifecycle
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0013_telegram_proxy_workspace"
down_revision = "0012_staff_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_proxies",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_telegram_proxies_organization_id",
        "telegram_proxies",
        ["organization_id"],
    )
    op.drop_index("uq_telegram_proxies_one_default", table_name="telegram_proxies")
    op.create_index(
        "uq_telegram_proxies_one_default_per_organization",
        "telegram_proxies",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_telegram_proxies_one_default_per_organization",
        table_name="telegram_proxies",
    )
    op.create_index(
        "uq_telegram_proxies_one_default",
        "telegram_proxies",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.drop_index("ix_telegram_proxies_organization_id", table_name="telegram_proxies")
    op.drop_column("telegram_proxies", "organization_id")
