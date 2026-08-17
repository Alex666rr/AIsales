"""Append-only, secret-safe audit event value objects."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from uuid import UUID, uuid4

from ..shared.outbox import safe_metadata


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Immutable description of a completed security-relevant action."""

    event_id: UUID
    organization_id: UUID
    actor_id: UUID
    action: str
    resource_type: str
    resource_id: UUID
    metadata: Mapping[str, object]

    @classmethod
    def create(
        cls,
        *,
        organization_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        metadata: Mapping[str, object],
    ) -> "AuditEvent":
        if not isinstance(action, str) or not action.strip():
            raise ValueError("audit action is required")
        if not isinstance(resource_type, str) or not resource_type.strip():
            raise ValueError("audit resource type is required")
        return cls(
            event_id=uuid4(),
            organization_id=organization_id,
            actor_id=actor_id,
            action=action.strip(),
            resource_type=resource_type.strip(),
            resource_id=resource_id,
            metadata=MappingProxyType(safe_metadata(metadata)),
        )
