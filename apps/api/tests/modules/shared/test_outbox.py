"""Contracts for tenant-scoped transactional outbox messages."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.modules.shared.commands import TenantContext
from app.modules.shared.outbox import OutboxMessage, SqlAlchemyUnitOfWork, outbox_messages
from app.modules.audit.models import AuditEvent
from app.modules.audit.service import audit_events


ORGANIZATION_ID = UUID("10000000-0000-0000-0000-000000000001")
ACTOR_ID = UUID("20000000-0000-0000-0000-000000000001")


def test_tenant_context_freezes_roles_after_issuance():
    """Mutable roles would let a caller elevate a context after it was issued."""
    context = TenantContext(
        organization_id=ORGANIZATION_ID,
        actor_id=ACTOR_ID,
        roles=frozenset({"manager"}),
    )

    with pytest.raises(AttributeError):
        context.roles = frozenset({"org:admin"})


def test_outbox_message_requires_non_blank_idempotency_key():
    """A blank key would allow a retry to create a second business effect."""
    with pytest.raises(ValueError, match="idempotency"):
        OutboxMessage.create(
            organization_id=ORGANIZATION_ID,
            topic="contact.imported",
            idempotency_key="   ",
            payload={"contact_id": "10000000-0000-0000-0000-000000000010"},
        )


def test_outbox_message_drops_sensitive_payload_values():
    """Persisting a session secret in a later worker payload would leak Telegram access."""
    message = OutboxMessage.create(
        organization_id=ORGANIZATION_ID,
        topic="telegram.connection.created",
        idempotency_key="connection:10000000-0000-0000-0000-000000000010",
        payload={"account_id": "10000000-0000-0000-0000-000000000010", "session": "raw-secret"},
    )

    assert message.payload == {"account_id": "10000000-0000-0000-0000-000000000010"}


def test_unit_of_work_commits_outbox_and_audit_together(tmp_path):
    """Committing only one record would make an external effect impossible to investigate."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'foundation.db'}", future=True)
    Base.metadata.create_all(engine, tables=[outbox_messages, audit_events])
    sessions = sessionmaker(engine, expire_on_commit=False)
    message = OutboxMessage.create(
        organization_id=ORGANIZATION_ID,
        topic="contact.imported",
        idempotency_key="contact:10000000-0000-0000-0000-000000000010",
        payload={"contact_id": "10000000-0000-0000-0000-000000000010"},
    )
    event = AuditEvent.create(
        organization_id=ORGANIZATION_ID,
        actor_id=ACTOR_ID,
        action="contact.imported",
        resource_type="contact",
        resource_id=UUID("10000000-0000-0000-0000-000000000010"),
        metadata={"source": "csv"},
    )

    with SqlAlchemyUnitOfWork(sessions) as work:
        work.outbox.enqueue(message)
        work.audit.append(event)

    with sessions() as session:
        assert session.execute(select(outbox_messages.c.message_id)).scalars().all() == [message.message_id]
        assert session.execute(select(audit_events.c.event_id)).scalars().all() == [event.event_id]
    engine.dispose()


def test_unit_of_work_rolls_back_outbox_and_audit_together(tmp_path):
    """A failed command must not leave a worker message or misleading audit trail behind."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'foundation.db'}", future=True)
    Base.metadata.create_all(engine, tables=[outbox_messages, audit_events])
    sessions = sessionmaker(engine, expire_on_commit=False)
    message = OutboxMessage.create(
        organization_id=ORGANIZATION_ID,
        topic="contact.imported",
        idempotency_key="contact:10000000-0000-0000-0000-000000000011",
        payload={"contact_id": "10000000-0000-0000-0000-000000000011"},
    )
    event = AuditEvent.create(
        organization_id=ORGANIZATION_ID,
        actor_id=ACTOR_ID,
        action="contact.imported",
        resource_type="contact",
        resource_id=UUID("10000000-0000-0000-0000-000000000011"),
        metadata={"source": "csv"},
    )

    with pytest.raises(RuntimeError, match="abort"):
        with SqlAlchemyUnitOfWork(sessions) as work:
            work.outbox.enqueue(message)
            work.audit.append(event)
            raise RuntimeError("abort")

    with sessions() as session:
        assert session.execute(select(outbox_messages.c.message_id)).scalars().all() == []
        assert session.execute(select(audit_events.c.event_id)).scalars().all() == []
    engine.dispose()
