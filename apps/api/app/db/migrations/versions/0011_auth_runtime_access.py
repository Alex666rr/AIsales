"""Grant the runtime role the explicit access required by authentication flows.

Revision ID: 0011_auth_runtime_access
Revises: 0010_totp_enrollment
Create Date: 2026-08-17
"""

from alembic import op


revision = "0011_auth_runtime_access"
down_revision = "0010_totp_enrollment"
branch_labels = None
depends_on = None


_AUTH_TABLES = (
    "organizations",
    "app_users",
    "auth_setup_invitations",
    "auth_totp_enrollments",
    "auth_sessions",
)
_RUNTIME_ROLE = "ai_sales_runtime"


def upgrade() -> None:
    for table_name in _AUTH_TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table_name} FROM PUBLIC")
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{table_name} TO {_RUNTIME_ROLE}"
        )


def downgrade() -> None:
    for table_name in _AUTH_TABLES:
        op.execute(
            f"REVOKE SELECT, INSERT, UPDATE ON TABLE public.{table_name} FROM {_RUNTIME_ROLE}"
        )
