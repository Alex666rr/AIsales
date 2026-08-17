"""Persist short-lived encrypted TOTP enrollment challenges.

Revision ID: 0010_totp_enrollment
Revises: 0009_global_user_email
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_totp_enrollment"
down_revision = "0009_global_user_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_totp_enrollments",
        sa.Column("enrollment_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=512), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("enrollment_id", name="pk_auth_totp_enrollments"),
        sa.UniqueConstraint("user_id", name="uq_auth_totp_enrollments_user_id"),
    )
    op.create_index("ix_auth_totp_enrollments_expires_at", "auth_totp_enrollments", ["expires_at"])
    op.execute("REVOKE UPDATE, DELETE ON TABLE auth_totp_enrollments FROM PUBLIC")


def downgrade() -> None:
    op.drop_index("ix_auth_totp_enrollments_expires_at", table_name="auth_totp_enrollments")
    op.drop_table("auth_totp_enrollments")
