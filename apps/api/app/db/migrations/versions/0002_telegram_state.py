"""Authoritative encrypted session, connection, and proxy state.

Revision ID: 0002_telegram_state
Revises: 0001_policy_gate
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_telegram_state"
down_revision = "0001_policy_gate"
branch_labels = None
depends_on = None


_RUNTIME_ROLE = "ai_sales_runtime"
_STATE_TABLES = (
    "telegram_accounts",
    "telegram_session_ciphertexts",
    "telegram_connections",
    "telegram_proxies",
    "telegram_proxy_overrides",
    "telegram_proxy_assignments",
)
_GATEWAY_TABLES = ("telegram_message_deliveries", "telegram_compatibility_rows")
_POLICY_TABLES = (
    "ai_approval_records",
    "ai_approval_revocations",
    "ai_approval_audit_events",
)


def upgrade() -> None:
    op.create_table(
        "telegram_accounts",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("account_id", name="pk_telegram_accounts"),
    )
    op.create_index("ix_telegram_accounts_organization_id", "telegram_accounts", ["organization_id"])
    op.create_table(
        "telegram_session_ciphertexts",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("key_version > 0", name="ck_telegram_session_ciphertexts_key_version"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["telegram_accounts.account_id"],
            name="fk_telegram_session_ciphertexts_account", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_id", name="pk_telegram_session_ciphertexts"),
        sa.UniqueConstraint(
            "account_id", "session_id", "key_version",
            name="uq_telegram_session_ciphertexts_account_session_key",
        ),
    )
    op.create_table(
        "telegram_connections",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("proxy_ip", sa.String(length=64)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lease_owner_id", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("fence_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "state IN ('quarantine', 'active', 'paused', 'reauth_required', 'limited', 'blocked', 'archived')",
            name="ck_telegram_connections_state",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_telegram_connections_retry_count"),
        sa.CheckConstraint("version >= 0", name="ck_telegram_connections_version"),
        sa.CheckConstraint("fence_token >= 0", name="ck_telegram_connections_fence_token"),
        sa.ForeignKeyConstraint(
            ["account_id", "session_id", "key_version"],
            [
                "telegram_session_ciphertexts.account_id",
                "telegram_session_ciphertexts.session_id",
                "telegram_session_ciphertexts.key_version",
            ],
            name="fk_telegram_connections_account_session_key", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("account_id", name="pk_telegram_connections"),
    )
    op.create_table(
        "telegram_proxies",
        sa.Column("proxy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.String(length=512), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("credential_key_version", sa.Integer()),
        sa.Column("credential_ciphertext", postgresql.BYTEA()),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("capacity BETWEEN 1 AND 5", name="ck_telegram_proxies_capacity"),
        sa.CheckConstraint(
            "(credential_key_version IS NULL) = (credential_ciphertext IS NULL)",
            name="ck_telegram_proxies_credentials_pair",
        ),
        sa.PrimaryKeyConstraint("proxy_id", name="pk_telegram_proxies"),
    )
    op.create_index(
        "uq_telegram_proxies_one_default",
        "telegram_proxies",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.create_table(
        "telegram_proxy_overrides",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proxy_id", postgresql.UUID(as_uuid=True)),
        sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_telegram_proxy_overrides_revision"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["telegram_accounts.account_id"],
            name="fk_telegram_proxy_overrides_account", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proxy_id"], ["telegram_proxies.proxy_id"],
            name="fk_telegram_proxy_overrides_proxy", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("account_id", name="pk_telegram_proxy_overrides"),
    )
    op.create_table(
        "telegram_proxy_assignments",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proxy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_revision", sa.BigInteger(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("assignment_revision >= 0", name="ck_telegram_proxy_assignments_revision"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["telegram_accounts.account_id"],
            name="fk_telegram_proxy_assignments_account", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proxy_id"], ["telegram_proxies.proxy_id"],
            name="fk_telegram_proxy_assignments_proxy", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("account_id", name="pk_telegram_proxy_assignments"),
        sa.UniqueConstraint("assignment_id", name="uq_telegram_proxy_assignments_assignment_id"),
    )
    op.create_index("ix_telegram_proxy_assignments_proxy_id", "telegram_proxy_assignments", ["proxy_id"])

    op.execute("GRANT USAGE ON SCHEMA public TO ai_sales_runtime")
    for table_name in _STATE_TABLES + _GATEWAY_TABLES + _POLICY_TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table_name} FROM PUBLIC")
    for table_name in _STATE_TABLES + _GATEWAY_TABLES:
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{table_name} TO {_RUNTIME_ROLE}"
        )
    for table_name in _POLICY_TABLES:
        op.execute(f"GRANT SELECT ON TABLE public.{table_name} TO {_RUNTIME_ROLE}")
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION public.policy_grant_ai_approval(
            uuid, uuid, uuid, varchar[], varchar[], varchar[], varchar, varchar, uuid, timestamptz
        ) TO ai_sales_runtime
        """
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.policy_revoke_ai_approval(uuid, uuid, uuid) TO ai_sales_runtime"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE EXECUTE ON FUNCTION public.policy_revoke_ai_approval(uuid, uuid, uuid) FROM ai_sales_runtime"
    )
    op.execute(
        """
        REVOKE EXECUTE ON FUNCTION public.policy_grant_ai_approval(
            uuid, uuid, uuid, varchar[], varchar[], varchar[], varchar, varchar, uuid, timestamptz
        ) FROM ai_sales_runtime
        """
    )
    for table_name in _GATEWAY_TABLES + _POLICY_TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table_name} FROM {_RUNTIME_ROLE}")
    op.drop_index("ix_telegram_proxy_assignments_proxy_id", table_name="telegram_proxy_assignments")
    op.drop_table("telegram_proxy_assignments")
    op.drop_table("telegram_proxy_overrides")
    op.drop_index("uq_telegram_proxies_one_default", table_name="telegram_proxies")
    op.drop_table("telegram_proxies")
    op.drop_table("telegram_connections")
    op.drop_table("telegram_session_ciphertexts")
    op.drop_index("ix_telegram_accounts_organization_id", table_name="telegram_accounts")
    op.drop_table("telegram_accounts")
