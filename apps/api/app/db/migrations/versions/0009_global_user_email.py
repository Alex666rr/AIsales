"""Make application-user e-mail globally unique.

Revision ID: 0009_global_user_email
Revises: 0008_owner_setup_invitations
Create Date: 2026-08-17
"""

from alembic import op


revision = "0009_global_user_email"
down_revision = "0008_owner_setup_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_app_users_organization_email", "app_users", type_="unique")
    op.create_unique_constraint("uq_app_users_email", "app_users", ["email"])


def downgrade() -> None:
    op.drop_constraint("uq_app_users_email", "app_users", type_="unique")
    op.create_unique_constraint(
        "uq_app_users_organization_email",
        "app_users",
        ["organization_id", "email"],
    )
