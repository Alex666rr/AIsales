"""Immutable Telegram/AI approval gate history.

Revision ID: 0001_policy_gate
Revises: 0001_telegram_gateway_durability
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_policy_gate"
down_revision = "0001_telegram_gateway_durability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_approval_records",
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_types", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("data_categories", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("operations", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("terms_revision", sa.String(length=128), nullable=False),
        sa.Column("evidence_uri", sa.String(length=2048), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cardinality(channel_types) > 0", name="ck_ai_approval_records_channels_present"),
        sa.CheckConstraint(
            "channel_types <@ ARRAY['mtproto_user', 'bot_api']::varchar[]",
            name="ck_ai_approval_records_channels_allowed",
        ),
        sa.CheckConstraint("cardinality(data_categories) > 0", name="ck_ai_approval_records_data_present"),
        sa.CheckConstraint(
            "data_categories <@ ARRAY['message_text', 'message_metadata', 'attachment_text', 'voice_transcript']::varchar[]",
            name="ck_ai_approval_records_data_allowed",
        ),
        sa.CheckConstraint("cardinality(operations) > 0", name="ck_ai_approval_records_operations_present"),
        sa.CheckConstraint(
            "operations <@ ARRAY['draft', 'auto_reply', 'summarize', 'classify']::varchar[]",
            name="ck_ai_approval_records_operations_allowed",
        ),
        sa.CheckConstraint("expires_at > approved_at", name="ck_ai_approval_records_valid_window"),
        sa.PrimaryKeyConstraint("approval_id", name="pk_ai_approval_records"),
    )
    op.create_index(
        "ix_ai_approval_records_scope_window",
        "ai_approval_records",
        ["organization_id", "terms_revision", "approved_at", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_approval_records_channels_gin",
        "ai_approval_records",
        ["channel_types"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_ai_approval_records_data_gin",
        "ai_approval_records",
        ["data_categories"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_ai_approval_records_operations_gin",
        "ai_approval_records",
        ["operations"],
        unique=False,
        postgresql_using="gin",
    )

    op.create_table(
        "ai_approval_revocations",
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["ai_approval_records.approval_id"],
            name="fk_ai_approval_revocations_record",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("approval_id", name="pk_ai_approval_revocations"),
    )
    op.create_table(
        "ai_approval_audit_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("action IN ('created', 'revoked')", name="ck_ai_approval_audit_events_action"),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["ai_approval_records.approval_id"],
            name="fk_ai_approval_audit_events_record",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_ai_approval_audit_events"),
    )
    op.create_index(
        "ix_ai_approval_audit_events_approval_time",
        "ai_approval_audit_events",
        ["approval_id", "occurred_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION deny_ai_approval_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'AI approval history is append-only';
        END;
        $$
        """
    )
    for table_name in (
        "ai_approval_records",
        "ai_approval_revocations",
        "ai_approval_audit_events",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION deny_ai_approval_history_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "ai_approval_audit_events",
        "ai_approval_revocations",
        "ai_approval_records",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS deny_ai_approval_history_mutation()")
    op.drop_index("ix_ai_approval_audit_events_approval_time", table_name="ai_approval_audit_events")
    op.drop_table("ai_approval_audit_events")
    op.drop_table("ai_approval_revocations")
    op.drop_index("ix_ai_approval_records_operations_gin", table_name="ai_approval_records")
    op.drop_index("ix_ai_approval_records_data_gin", table_name="ai_approval_records")
    op.drop_index("ix_ai_approval_records_channels_gin", table_name="ai_approval_records")
    op.drop_index("ix_ai_approval_records_scope_window", table_name="ai_approval_records")
    op.drop_table("ai_approval_records")
