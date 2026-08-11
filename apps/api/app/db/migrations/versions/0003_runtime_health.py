"""Permit the runtime role to read only the Alembic readiness revision.

Revision ID: 0003_runtime_health
Revises: 0002_telegram_state
Create Date: 2026-08-11
"""

from alembic import op


revision = "0003_runtime_health"
down_revision = "0002_telegram_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE ALL ON TABLE public.alembic_version FROM PUBLIC")
    op.execute("GRANT SELECT ON TABLE public.alembic_version TO ai_sales_runtime")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE public.alembic_version FROM ai_sales_runtime")
