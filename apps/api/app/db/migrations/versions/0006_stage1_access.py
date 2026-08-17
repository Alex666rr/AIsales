"""Create provisioned organizations, users, and server-side sessions.

Revision ID: 0006_stage1_access
Revises: 0005_stage1_foundation
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_stage1_access"
down_revision = "0005_stage1_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("organization_id", name="pk_organizations"),
        sa.UniqueConstraint("name", name="uq_organizations_name"),
    )
    op.create_table(
        "app_users",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("encrypted_totp_secret", sa.Text(), nullable=True),
        sa.Column("recovery_code_hashes", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('platform_owner', 'company_owner', 'administrator', 'manager')",
            name="ck_app_users_role",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_app_users"),
        sa.UniqueConstraint("organization_id", "email", name="uq_app_users_organization_email"),
    )
    op.create_index("ix_app_users_organization_id", "app_users", ["organization_id"])
    op.create_table(
        "auth_sessions",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("mfa_verified", sa.Boolean(), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.organization_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("session_id", name="pk_auth_sessions"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_organization_id", "auth_sessions", ["organization_id"])
    op.execute("REVOKE UPDATE, DELETE ON TABLE organizations FROM PUBLIC")
    op.execute("REVOKE UPDATE, DELETE ON TABLE app_users FROM PUBLIC")
    op.execute("REVOKE UPDATE, DELETE ON TABLE auth_sessions FROM PUBLIC")


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_organization_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_app_users_organization_id", table_name="app_users")
    op.drop_table("app_users")
    op.drop_table("organizations")
