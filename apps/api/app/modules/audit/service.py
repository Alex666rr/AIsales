"""SQLAlchemy writer for append-only audit events."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ...db.base import Base
from .models import AuditEvent


audit_events = sa.Table(
    "audit_events",
    Base.metadata,
    sa.Column("event_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False, index=True),
    sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=False),
    sa.Column("action", sa.String(128), nullable=False),
    sa.Column("resource_type", sa.String(128), nullable=False),
    sa.Column("resource_id", sa.Uuid(as_uuid=True), nullable=False),
    sa.Column("metadata", sa.JSON(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
)


class SqlAlchemyAuditWriter:
    """Append audit events through the unit-of-work transaction only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: AuditEvent) -> None:
        self._session.execute(
            sa.insert(audit_events).values(
                event_id=event.event_id,
                organization_id=event.organization_id,
                actor_id=event.actor_id,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                metadata=dict(event.metadata),
            )
        )
