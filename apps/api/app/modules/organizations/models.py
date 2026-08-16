"""Organization membership value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class UserRole(StrEnum):
    """Roles granted by an organization administrator."""

    PLATFORM_OWNER = "platform_owner"
    COMPANY_OWNER = "company_owner"
    ADMINISTRATOR = "administrator"
    MANAGER = "manager"


@dataclass(frozen=True, slots=True)
class Organization:
    """A named tenant boundary provisioned by the platform owner."""

    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class OrganizationMember:
    """A user membership bound to exactly one organization."""

    user_id: UUID
    organization_id: UUID
    email: str
    role: UserRole
