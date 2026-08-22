"""Session-scoped organization administration views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.modules.auth.models import AuthUser
from app.modules.organizations.models import UserRole
from app.modules.organizations.provisioning import StaffManagementService
from app.modules.organizations.service import OrganizationPermissionDenied
from app.modules.shared.commands import TenantContext


@dataclass(frozen=True, slots=True)
class OrganizationProfile:
    organization_id: UUID
    name: str


class WorkspaceOrganizationRepository(Protocol):
    def get_organization_name(self, organization_id: UUID) -> str | None: ...
    def rename_organization(self, organization_id: UUID, name: str) -> str | None: ...


class WorkspaceOrganizationService:
    def __init__(self, repository: WorkspaceOrganizationRepository, staff: StaffManagementService) -> None:
        self._repository = repository
        self._staff = staff

    def profile(self, context: TenantContext) -> OrganizationProfile:
        name = self._repository.get_organization_name(context.organization_id)
        if name is None:
            raise LookupError("organization was not found")
        return OrganizationProfile(context.organization_id, name)

    def rename(self, context: TenantContext, name: str) -> OrganizationProfile:
        self._require_owner(context)
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("organization name is required")
        saved = self._repository.rename_organization(context.organization_id, cleaned)
        if saved is None:
            raise LookupError("organization was not found")
        return OrganizationProfile(context.organization_id, saved)

    def members(self, context: TenantContext) -> tuple[AuthUser, ...]:
        return self._staff.list_members(context)

    def deactivate(self, context: TenantContext, user_id: UUID) -> AuthUser:
        return self._staff.deactivate_member(context, user_id)

    @staticmethod
    def _require_owner(context: TenantContext) -> None:
        if UserRole.COMPANY_OWNER.value not in context.roles:
            raise OrganizationPermissionDenied("company owner role is required")
