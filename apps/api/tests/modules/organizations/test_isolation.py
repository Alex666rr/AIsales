"""Tenant isolation contracts for organization membership."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.modules.organizations.models import Organization, OrganizationMember, UserRole
from app.modules.organizations.service import (
    OrganizationPermissionDenied,
    OrganizationService,
    OrganizationUserNotFound,
)
from app.modules.shared.commands import TenantContext


ORG_A = UUID("10000000-0000-0000-0000-000000000001")
ORG_B = UUID("10000000-0000-0000-0000-000000000002")
OWNER_A = UUID("20000000-0000-0000-0000-000000000001")
USER_B = UUID("30000000-0000-0000-0000-000000000002")


def test_organization_is_an_immutable_tenant_boundary():
    organization = Organization(id=ORG_A, name="Acme")

    assert organization.id == ORG_A
    with pytest.raises(AttributeError):
        organization.name = "Other"  # type: ignore[misc]


class FakeOrganizationRepository:
    """Test double; production storage is injected through the repository protocol."""

    def __init__(self) -> None:
        self.members: dict[UUID, OrganizationMember] = {}

    def create_member(self, member: OrganizationMember) -> OrganizationMember:
        self.members[member.user_id] = member
        return member

    def get_member(self, user_id: UUID) -> OrganizationMember | None:
        return self.members.get(user_id)

    def put_member(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        email: str,
        role: UserRole,
    ) -> None:
        self.members[user_id] = OrganizationMember(
            user_id=user_id,
            organization_id=organization_id,
            email=email,
            role=role,
        )


def company_owner_context() -> TenantContext:
    return TenantContext(organization_id=ORG_A, actor_id=OWNER_A, roles=frozenset({"company_owner"}))


def manager_context() -> TenantContext:
    return TenantContext(organization_id=ORG_A, actor_id=OWNER_A, roles=frozenset({"manager"}))


def test_company_owner_creates_manager_inside_own_organization():
    """Using a caller-selected organization ID would let an owner provision another tenant."""
    service = OrganizationService(FakeOrganizationRepository())

    member = service.create_member(
        company_owner_context(),
        email="manager@example.test",
        role=UserRole.MANAGER,
    )

    assert member.organization_id == ORG_A
    assert member.role is UserRole.MANAGER


def test_cross_tenant_member_lookup_is_neutral_not_found():
    """Returning a foreign record or forbidden error would expose another organization's users."""
    repository = FakeOrganizationRepository()
    repository.put_member(
        user_id=USER_B,
        organization_id=ORG_B,
        email="foreign@example.test",
        role=UserRole.MANAGER,
    )
    service = OrganizationService(repository)

    with pytest.raises(OrganizationUserNotFound):
        service.get_member(company_owner_context(), USER_B)


def test_manager_cannot_create_an_organization_member():
    """Only the company owner can change access inside its organization."""
    service = OrganizationService(FakeOrganizationRepository())

    with pytest.raises(OrganizationPermissionDenied):
        service.create_member(
            manager_context(),
            email="other@example.test",
            role=UserRole.MANAGER,
        )
