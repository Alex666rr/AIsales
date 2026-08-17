"""Persist one-time setup invitations for first company owners.

Revision ID: 0008_owner_setup_invitations
Revises: 0007_auth_session_lifecycle
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_owner_setup_invitations"
down_revision = "0007_auth_session_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "app_users",
        "password_hash",
        existing_type=sa.String(length=512),
        nullable=True,
    )
    op.create_table(
        "auth_setup_invitations",
        sa.Column("invitation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("invitation_id", name="pk_auth_setup_invitations"),
        sa.UniqueConstraint("user_id", name="uq_auth_setup_invitations_user_id"),
    )
    op.create_index("ix_auth_setup_invitations_expires_at", "auth_setup_invitations", ["expires_at"])
    op.execute("REVOKE UPDATE, DELETE ON TABLE auth_setup_invitations FROM PUBLIC")


def downgrade() -> None:
    op.drop_index("ix_auth_setup_invitations_expires_at", table_name="auth_setup_invitations")
    op.drop_table("auth_setup_invitations")
    op.alter_column(
        "app_users",
        "password_hash",
        existing_type=sa.String(length=512),
        nullable=False,
    )
