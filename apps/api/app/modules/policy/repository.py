"""PostgreSQL source of truth for immutable AI approval history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...db.base import Base
from .models import (
    AiApprovalRecord,
    AiOperationContext,
    ApprovalGrantRequest,
    ChannelType,
    DataCategory,
    AiOperation,
    TermsRevision,
)


ai_approval_records = sa.Table(
    "ai_approval_records",
    Base.metadata,
    sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("channel_types", postgresql.ARRAY(sa.String(32)), nullable=False),
    sa.Column("data_categories", postgresql.ARRAY(sa.String(64)), nullable=False),
    sa.Column("operations", postgresql.ARRAY(sa.String(32)), nullable=False),
    sa.Column("terms_revision", sa.String(128), nullable=False),
    sa.Column("evidence_uri", sa.String(2048), nullable=False),
    sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
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
sa.Index(
    "ix_ai_approval_records_scope_window",
    ai_approval_records.c.organization_id,
    ai_approval_records.c.terms_revision,
    ai_approval_records.c.approved_at,
    ai_approval_records.c.expires_at,
)
sa.Index("ix_ai_approval_records_channels_gin", ai_approval_records.c.channel_types, postgresql_using="gin")
sa.Index("ix_ai_approval_records_data_gin", ai_approval_records.c.data_categories, postgresql_using="gin")
sa.Index("ix_ai_approval_records_operations_gin", ai_approval_records.c.operations, postgresql_using="gin")

ai_approval_revocations = sa.Table(
    "ai_approval_revocations",
    Base.metadata,
    sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    sa.ForeignKeyConstraint(
        ["approval_id"],
        ["ai_approval_records.approval_id"],
        name="fk_ai_approval_revocations_record",
        ondelete="RESTRICT",
    ),
    sa.PrimaryKeyConstraint("approval_id", name="pk_ai_approval_revocations"),
)

ai_approval_audit_events = sa.Table(
    "ai_approval_audit_events",
    Base.metadata,
    sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("action", sa.String(16), nullable=False),
    sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    sa.CheckConstraint("action IN ('created', 'revoked')", name="ck_ai_approval_audit_events_action"),
    sa.ForeignKeyConstraint(
        ["approval_id"],
        ["ai_approval_records.approval_id"],
        name="fk_ai_approval_audit_events_record",
        ondelete="RESTRICT",
    ),
    sa.PrimaryKeyConstraint("event_id", name="pk_ai_approval_audit_events"),
)
sa.Index(
    "ix_ai_approval_audit_events_approval_time",
    ai_approval_audit_events.c.approval_id,
    ai_approval_audit_events.c.occurred_at,
)


class ApprovalRepositoryUnavailable(RuntimeError):
    """Safe repository failure that contains no database details."""


class ApprovalWriteRejected(RuntimeError):
    """Safe administrative rejection for an invalid or missing approval."""


@dataclass(frozen=True)
class ApprovalRepositorySnapshot:
    """A candidate row and the database/repository time used to read it."""

    checked_at: datetime
    record: AiApprovalRecord | None


class ApprovalRepository(Protocol):
    """Content-free persistence boundary used by policy services."""

    async def find_matching(
        self,
        context: AiOperationContext,
        terms_revision: TermsRevision,
    ) -> ApprovalRepositorySnapshot:
        """Return a candidate that matches every requested dimension at repository time."""


class _ApprovalWriter(Protocol):
    """Internal mutation dependency available only to the administration service."""

    async def create(self, request: ApprovalGrantRequest, approved_by: UUID) -> AiApprovalRecord:
        """Atomically append an approval and its audit event."""

    async def revoke(self, approval_id: UUID, revoked_by: UUID) -> AiApprovalRecord:
        """Atomically append a distinct revocation and audit event."""


class SqlAlchemyApprovalRepository:
    """Public read-only PostgreSQL approval query boundary."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_matching(
        self,
        context: AiOperationContext,
        terms_revision: TermsRevision,
    ) -> ApprovalRepositorySnapshot:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    checked_at = (await session.execute(sa.select(sa.func.current_timestamp()))).scalar_one()
                    row = (
                        await session.execute(self._matching_statement(context, terms_revision))
                    ).mappings().one_or_none()
            if row is None:
                return ApprovalRepositorySnapshot(checked_at=checked_at, record=None)
            return ApprovalRepositorySnapshot(checked_at=checked_at, record=self._record_from_mapping(row))
        except Exception as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ApprovalRepositoryUnavailable("approval repository unavailable") from None

    @staticmethod
    def _matching_statement(context: AiOperationContext, terms_revision: TermsRevision) -> sa.Select:
        joined = ai_approval_records.outerjoin(
            ai_approval_revocations,
            ai_approval_revocations.c.approval_id == ai_approval_records.c.approval_id,
        )
        now = sa.func.current_timestamp()
        return (
            sa.select(*ai_approval_records.c, ai_approval_revocations.c.revoked_at)
            .select_from(joined)
            .where(
                ai_approval_records.c.organization_id == context.organization_id,
                ai_approval_records.c.channel_types.contains([context.channel_type.value]),
                ai_approval_records.c.data_categories.contains([context.data_category.value]),
                ai_approval_records.c.operations.contains([context.operation.value]),
                ai_approval_records.c.terms_revision == str(terms_revision),
                ai_approval_records.c.approved_at <= now,
                ai_approval_records.c.expires_at > now,
                ai_approval_revocations.c.approval_id.is_(None),
            )
            .limit(1)
        )

    @staticmethod
    def _record_from_mapping(mapping, *, revoked_at: datetime | None = None) -> AiApprovalRecord:
        if revoked_at is None:
            revoked_at = mapping.get("revoked_at")
        return AiApprovalRecord(
            approval_id=mapping["approval_id"],
            organization_id=mapping["organization_id"],
            channel_types=frozenset(ChannelType(value) for value in mapping["channel_types"]),
            data_categories=frozenset(DataCategory(value) for value in mapping["data_categories"]),
            operations=frozenset(AiOperation(value) for value in mapping["operations"]),
            terms_revision=TermsRevision(mapping["terms_revision"]),
            evidence_uri=mapping["evidence_uri"],
            approved_by=mapping["approved_by"],
            approved_at=mapping["approved_at"],
            expires_at=mapping["expires_at"],
            revoked_at=revoked_at,
        )


class _SqlAlchemyApprovalWriter:
    """Private PostgreSQL mutation adapter used only behind trusted administration."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, request: ApprovalGrantRequest, approved_by: UUID) -> AiApprovalRecord:
        approval_id = uuid4()
        event_id = uuid4()
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            self._grant_statement(
                                request=request,
                                approval_id=approval_id,
                                event_id=event_id,
                                actor_id=approved_by,
                            )
                        )
                    ).mappings().one_or_none()
                    if row is None:
                        raise ApprovalWriteRejected("approval expiry must be in the future")
            return SqlAlchemyApprovalRepository._record_from_mapping(row)
        except ApprovalWriteRejected:
            raise
        except (SQLAlchemyError, ValueError, TypeError):
            raise ApprovalRepositoryUnavailable("approval repository unavailable") from None

    async def revoke(self, approval_id: UUID, revoked_by: UUID) -> AiApprovalRecord:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            self._revoke_statement(
                                approval_id=approval_id,
                                event_id=uuid4(),
                                actor_id=revoked_by,
                            )
                        )
                    ).mappings().one_or_none()
                    if row is None:
                        raise ApprovalWriteRejected("approval not found")
            return SqlAlchemyApprovalRepository._record_from_mapping(row)
        except ApprovalWriteRejected:
            raise
        except (SQLAlchemyError, ValueError, TypeError):
            raise ApprovalRepositoryUnavailable("approval repository unavailable") from None

    @staticmethod
    def _grant_statement(
        *,
        request: ApprovalGrantRequest,
        approval_id: UUID,
        event_id: UUID,
        actor_id: UUID,
    ) -> sa.sql.elements.TextClause:
        return sa.text(
            """
            SELECT * FROM public.policy_grant_ai_approval(
                :approval_id, :event_id, :organization_id, :channel_types,
                :data_categories, :operations, :terms_revision,
                :evidence_uri, :actor_id, :expires_at
            )
            """
        ).bindparams(
            sa.bindparam("approval_id", value=approval_id, type_=postgresql.UUID(as_uuid=True)),
            sa.bindparam("event_id", value=event_id, type_=postgresql.UUID(as_uuid=True)),
            sa.bindparam("organization_id", value=request.organization_id, type_=postgresql.UUID(as_uuid=True)),
            sa.bindparam(
                "channel_types",
                value=sorted(item.value for item in request.channel_types),
                type_=postgresql.ARRAY(sa.String(32)),
            ),
            sa.bindparam(
                "data_categories",
                value=sorted(item.value for item in request.data_categories),
                type_=postgresql.ARRAY(sa.String(64)),
            ),
            sa.bindparam(
                "operations",
                value=sorted(item.value for item in request.operations),
                type_=postgresql.ARRAY(sa.String(32)),
            ),
            sa.bindparam("terms_revision", value=str(request.terms_revision), type_=sa.String(128)),
            sa.bindparam("evidence_uri", value=request.evidence_uri, type_=sa.String(2048)),
            sa.bindparam("actor_id", value=actor_id, type_=postgresql.UUID(as_uuid=True)),
            sa.bindparam("expires_at", value=request.expires_at, type_=sa.DateTime(timezone=True)),
        )

    @staticmethod
    def _revoke_statement(
        *,
        approval_id: UUID,
        event_id: UUID,
        actor_id: UUID,
    ) -> sa.sql.elements.TextClause:
        return sa.text(
            """
            SELECT * FROM public.policy_revoke_ai_approval(
                :approval_id, :event_id, :actor_id
            )
            """
        ).bindparams(
            sa.bindparam("approval_id", value=approval_id, type_=postgresql.UUID(as_uuid=True)),
            sa.bindparam("event_id", value=event_id, type_=postgresql.UUID(as_uuid=True)),
            sa.bindparam("actor_id", value=actor_id, type_=postgresql.UUID(as_uuid=True)),
        )
