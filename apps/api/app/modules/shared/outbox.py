"""Safe envelope for work that is persisted before later delivery."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from ...db.base import Base


_SENSITIVE_KEY_PARTS = frozenset({"api_hash", "password", "secret", "session", "token", "tdata"})


outbox_messages = sa.Table(
    "outbox_messages",
    Base.metadata,
    sa.Column("message_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False, index=True),
    sa.Column("topic", sa.String(128), nullable=False),
    sa.Column("idempotency_key", sa.String(256), nullable=False, unique=True),
    sa.Column("payload", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
)


def safe_metadata(values: Mapping[str, object]) -> dict[str, object]:
    """Drop values whose field names can contain authentication material."""
    return {
        key: value
        for key, value in values.items()
        if not any(part in key.lower() for part in _SENSITIVE_KEY_PARTS)
    }


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    """Tenant-scoped, idempotent message stored before background handling."""

    message_id: UUID
    organization_id: UUID
    topic: str
    idempotency_key: str
    payload: Mapping[str, object]

    @classmethod
    def create(
        cls,
        *,
        organization_id: UUID,
        topic: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> "OutboxMessage":
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("outbox topic is required")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("outbox idempotency key is required")
        return cls(
            message_id=uuid4(),
            organization_id=organization_id,
            topic=topic.strip(),
            idempotency_key=idempotency_key.strip(),
            payload=MappingProxyType(safe_metadata(payload)),
        )


class SqlAlchemyOutboxRepository:
    """Append-only SQLAlchemy boundary for durable worker messages."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, message: OutboxMessage) -> None:
        self._session.execute(
            sa.insert(outbox_messages).values(
                message_id=message.message_id,
                organization_id=message.organization_id,
                topic=message.topic,
                idempotency_key=message.idempotency_key,
                payload=dict(message.payload),
            )
        )


class SqlAlchemyUnitOfWork:
    """Commit audit and outbox changes together or roll both back."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions
        self._session: Session | None = None
        self._transaction = None
        self.outbox: SqlAlchemyOutboxRepository
        self.audit: object

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        from ..audit.service import SqlAlchemyAuditWriter

        self._session = self._sessions()
        self._transaction = self._session.begin()
        self._transaction.__enter__()
        self.outbox = SqlAlchemyOutboxRepository(self._session)
        self.audit = SqlAlchemyAuditWriter(self._session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        assert self._session is not None and self._transaction is not None
        try:
            self._transaction.__exit__(exc_type, exc, traceback)
        finally:
            self._session.close()
            self._session = None
            self._transaction = None
        return False
