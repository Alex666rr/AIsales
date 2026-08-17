"""Add activity and expiry timestamps to server-side sessions."""

from alembic import op
import sqlalchemy as sa

revision = "0007_auth_session_lifecycle"
down_revision = "0006_stage1_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_sessions", sa.Column("last_active_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
    op.add_column("auth_sessions", sa.Column("expires_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_column("auth_sessions", "expires_at")
    op.drop_column("auth_sessions", "last_active_at")
