"""Durable Telegram gateway idempotency and compatibility evidence.

Revision ID: 0001_telegram_gateway_durability
Revises:
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_telegram_gateway_durability"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_message_deliveries",
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("peer_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("external_message_id", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("owner_id", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fence_token", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("account_id", "idempotency_key", name="pk_telegram_message_deliveries"),
    )
    op.create_table(
        "telegram_compatibility_rows",
        sa.Column("adapter", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        # Never NULL: PostgreSQL considers NULL unique keys distinct.
        sa.Column("proxy_key", sa.String(length=36), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("adapter", "adapter_version", "proxy_key", name="pk_telegram_compatibility_rows"),
    )


def downgrade() -> None:
    op.drop_table("telegram_compatibility_rows")
    op.drop_table("telegram_message_deliveries")
