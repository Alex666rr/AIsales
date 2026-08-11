"""Bind each managed connection to one Telegram numeric account identity.

Revision ID: 0004_telegram_connection_identity
Revises: 0003_runtime_health
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_telegram_connection_identity"
down_revision = "0003_runtime_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("telegram_accounts", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(
        "uq_telegram_accounts_telegram_user_id",
        "telegram_accounts",
        ["telegram_user_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_telegram_accounts_telegram_user_id",
        "telegram_accounts",
        type_="unique",
    )
    op.drop_column("telegram_accounts", "telegram_user_id")
