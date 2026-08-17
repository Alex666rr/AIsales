"""Create the tenant-scoped Stage 1 audit and outbox foundation.

Revision ID: 0005_stage1_foundation
Revises: 0004_telegram_identity
Create Date: 2026-08-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_stage1_foundation"
down_revision = "0004_telegram_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox_messages",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("message_id", name="pk_outbox_messages"),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_messages_idempotency_key"),
    )
    op.create_index("ix_outbox_messages_organization_id", "outbox_messages", ["organization_id"])
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_events"),
    )
    op.create_index("ix_audit_events_organization_id", "audit_events", ["organization_id"])
    op.execute("REVOKE UPDATE, DELETE ON TABLE outbox_messages FROM PUBLIC")
    op.execute("REVOKE UPDATE, DELETE ON TABLE audit_events FROM PUBLIC")


def downgrade() -> None:
    op.drop_index("ix_audit_events_organization_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_outbox_messages_organization_id", table_name="outbox_messages")
    op.drop_table("outbox_messages")
