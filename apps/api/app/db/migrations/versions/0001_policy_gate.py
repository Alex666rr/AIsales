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
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_truncate_immutable
            BEFORE TRUNCATE ON {table_name}
            FOR EACH STATEMENT EXECUTE FUNCTION deny_ai_approval_history_mutation()
            """
        )
        op.execute(
            f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE {table_name} FROM PUBLIC"
        )

    # EXECUTE privilege on these functions is the database authorization
    # boundary.  The actor UUID is audit metadata supplied only after the
    # application has revalidated a server-issued platform-owner capability.
    # This migration intentionally grants EXECUTE to no invented runtime role;
    # deployment must grant it to the separately configured trusted writer.
    op.execute(
        """
        CREATE FUNCTION public.policy_grant_ai_approval(
            p_approval_id uuid,
            p_event_id uuid,
            p_organization_id uuid,
            p_channel_types varchar[],
            p_data_categories varchar[],
            p_operations varchar[],
            p_terms_revision varchar,
            p_evidence_uri varchar,
            p_actor_id uuid,
            p_expires_at timestamptz
        )
        RETURNS TABLE (
            approval_id uuid,
            organization_id uuid,
            channel_types varchar[],
            data_categories varchar[],
            operations varchar[],
            terms_revision varchar,
            evidence_uri varchar,
            approved_by uuid,
            approved_at timestamptz,
            expires_at timestamptz,
            revoked_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $policy$
        DECLARE
            v_approved_at timestamptz := CURRENT_TIMESTAMP;
        BEGIN
            IF p_expires_at <= v_approved_at THEN
                RETURN;
            END IF;

            INSERT INTO public.ai_approval_records (
                approval_id, organization_id, channel_types, data_categories,
                operations, terms_revision, evidence_uri, approved_by,
                approved_at, expires_at
            ) VALUES (
                p_approval_id, p_organization_id, p_channel_types, p_data_categories,
                p_operations, p_terms_revision, p_evidence_uri, p_actor_id,
                v_approved_at, p_expires_at
            );
            INSERT INTO public.ai_approval_audit_events (
                event_id, approval_id, action, actor_id, occurred_at
            ) VALUES (
                p_event_id, p_approval_id, 'created', p_actor_id, v_approved_at
            );

            RETURN QUERY
            SELECT record.approval_id, record.organization_id, record.channel_types,
                   record.data_categories, record.operations, record.terms_revision,
                   record.evidence_uri, record.approved_by, record.approved_at,
                   record.expires_at, NULL::timestamptz
            FROM public.ai_approval_records AS record
            WHERE record.approval_id = p_approval_id;
        END;
        $policy$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.policy_revoke_ai_approval(
            p_approval_id uuid,
            p_event_id uuid,
            p_actor_id uuid
        )
        RETURNS TABLE (
            approval_id uuid,
            organization_id uuid,
            channel_types varchar[],
            data_categories varchar[],
            operations varchar[],
            terms_revision varchar,
            evidence_uri varchar,
            approved_by uuid,
            approved_at timestamptz,
            expires_at timestamptz,
            revoked_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $policy$
        DECLARE
            v_revoked_at timestamptz;
        BEGIN
            PERFORM 1
            FROM public.ai_approval_records AS record
            WHERE record.approval_id = p_approval_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN;
            END IF;

            INSERT INTO public.ai_approval_revocations AS revocation (
                approval_id, revoked_by, revoked_at
            ) VALUES (
                p_approval_id, p_actor_id, CURRENT_TIMESTAMP
            )
            ON CONFLICT ON CONSTRAINT pk_ai_approval_revocations DO NOTHING
            RETURNING revocation.revoked_at INTO v_revoked_at;

            IF v_revoked_at IS NOT NULL THEN
                INSERT INTO public.ai_approval_audit_events (
                    event_id, approval_id, action, actor_id, occurred_at
                ) VALUES (
                    p_event_id, p_approval_id, 'revoked', p_actor_id, v_revoked_at
                );
            ELSE
                SELECT revocation.revoked_at INTO v_revoked_at
                FROM public.ai_approval_revocations AS revocation
                WHERE revocation.approval_id = p_approval_id;
            END IF;

            RETURN QUERY
            SELECT record.approval_id, record.organization_id, record.channel_types,
                   record.data_categories, record.operations, record.terms_revision,
                   record.evidence_uri, record.approved_by, record.approved_at,
                   record.expires_at, v_revoked_at
            FROM public.ai_approval_records AS record
            WHERE record.approval_id = p_approval_id;
        END;
        $policy$
        """
    )
    op.execute(
        """
        REVOKE ALL ON FUNCTION public.policy_grant_ai_approval(
            uuid, uuid, uuid, varchar[], varchar[], varchar[], varchar, varchar, uuid, timestamptz
        ) FROM PUBLIC
        """
    )
    op.execute(
        """
        REVOKE ALL ON FUNCTION public.policy_revoke_ai_approval(uuid, uuid, uuid) FROM PUBLIC
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION IF EXISTS public.policy_revoke_ai_approval(uuid, uuid, uuid)
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS public.policy_grant_ai_approval(
            uuid, uuid, uuid, varchar[], varchar[], varchar[], varchar, varchar, uuid, timestamptz
        )
        """
    )
    for table_name in (
        "ai_approval_audit_events",
        "ai_approval_revocations",
        "ai_approval_records",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_truncate_immutable ON {table_name}")
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
