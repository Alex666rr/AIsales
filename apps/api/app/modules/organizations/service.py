"""Tenant-safe organization membership service."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from app.modules.organizations.models import OrganizationMember, UserRole
from app.modules.shared.commands import TenantContext


class OrganizationRepository(Protocol):
    """Persistence boundary for organization memberships."""

    def create_member(self, member: OrganizationMember) -> OrganizationMember: ...

    def get_member(self, user_id: UUID) -> OrganizationMember | None: ...


class OrganizationPermissionDenied(PermissionError):
    """The current tenant-bound actor is not allowed to administer members."""


class OrganizationUserNotFound(LookupError):
    """A user is absent from the actor's organization."""


class OrganizationService:
    """Provides organization-scoped member access without caller-selected tenants."""

    def __init__(self, repository: OrganizationRepository) -> None:
        self._repository = repository

    def create_member(
        self,
        context: TenantContext,
        *,
        email: str,
        role: UserRole,
    ) -> OrganizationMember:
        self._require_company_owner(context)
        if role not in {UserRole.ADMINISTRATOR, UserRole.MANAGER}:
            raise OrganizationPermissionDenied("company owners may only provision internal roles")

        return self._repository.create_member(
            OrganizationMember(
                user_id=uuid4(),
                organization_id=context.organization_id,
                email=email,
                role=role,
            )
        )

    def get_member(self, context: TenantContext, user_id: UUID) -> OrganizationMember:
        member = self._repository.get_member(user_id)
        if member is None or member.organization_id != context.organization_id:
            raise OrganizationUserNotFound("organization member was not found")
        return member

    @staticmethod
    def _require_company_owner(context: TenantContext) -> None:
        if UserRole.COMPANY_OWNER.value not in context.roles:
            raise OrganizationPermissionDenied("company owner role is required")
