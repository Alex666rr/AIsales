"""Shared, tenant-bound application command contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Server-issued tenant and actor identity used by application services."""

    organization_id: UUID
    actor_id: UUID
    roles: frozenset[str]
