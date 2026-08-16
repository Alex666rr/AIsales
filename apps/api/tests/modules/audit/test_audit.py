"""Contracts for append-only audit events."""

from __future__ import annotations

from uuid import UUID

from app.modules.audit.models import AuditEvent


ORGANIZATION_ID = UUID("10000000-0000-0000-0000-000000000001")
ACTOR_ID = UUID("20000000-0000-0000-0000-000000000001")


def test_audit_event_keeps_tenant_actor_and_safe_metadata():
    """Dropping tenant or actor attribution would make a later security investigation impossible."""
    event = AuditEvent.create(
        organization_id=ORGANIZATION_ID,
        actor_id=ACTOR_ID,
        action="telegram.connection.imported",
        resource_type="telegram_account",
        resource_id=UUID("30000000-0000-0000-0000-000000000001"),
        metadata={"source": "tdata", "telegram_api_hash": "must-not-persist"},
    )

    assert event.organization_id == ORGANIZATION_ID
    assert event.actor_id == ACTOR_ID
    assert event.metadata == {"source": "tdata"}


def test_audit_event_has_no_mutation_api():
    """Providing update or delete methods would violate the append-only audit contract."""
    assert not hasattr(AuditEvent, "update")
    assert not hasattr(AuditEvent, "delete")
