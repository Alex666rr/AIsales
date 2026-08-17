"""Persist staff deactivation and keep direct deletion unavailable.

Revision ID: 0012_staff_lifecycle
Revises: 0011_auth_runtime_access
Create Date: 2026-08-17
"""

import sqlalchemy as sa
from alembic import op


revision = "0012_staff_lifecycle"
down_revision = "0011_auth_runtime_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("REVOKE DELETE ON TABLE public.app_users FROM PUBLIC")


def downgrade() -> None:
    op.drop_column("app_users", "disabled_at")
