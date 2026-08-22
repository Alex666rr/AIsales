"""Tenant-safe organization administration HTTP contracts."""

from __future__ import annotations

from uuid import UUID
from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.organizations.models import UserRole
from app.modules.organizations.provisioning import ProvisionedMember, ProvisionedOwner
from app.modules.policy.models import PlatformOwnerPrincipal
from app.modules.organizations.service import OrganizationPermissionDenied, OrganizationService
from app.modules.organizations.workspace import WorkspaceOrganizationService
from app.modules.shared.commands import TenantContext


class CreateMemberRequest(BaseModel):
    """Target organization is deliberately absent; it comes from authentication context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: UserRole


class CreateOrganizationRequest(BaseModel):
    """Platform-owner input; roles and identifiers are always server-assigned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_name: str = Field(min_length=1, max_length=256)
    owner_email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProvisionedOwnerResponse(BaseModel):
    """The setup token is returned once to the authenticated platform owner only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: UUID
    owner_email: str
    setup_token: str = Field(repr=False)


class MemberResponse(BaseModel):
    """Safe membership view; password hashes and MFA material never leave storage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID
    email: str
    role: UserRole


class ProvisionedMemberResponse(MemberResponse):
    """One-time staff setup material, returned only to the authenticated owner."""

    setup_token: str = Field(repr=False)


class OrganizationProfileResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    organization_id: UUID
    name: str


class RenameOrganizationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1, max_length=256)


class WorkspaceMemberResponse(MemberResponse):
    is_active: bool


PrincipalDependency = Callable[[], Awaitable[TenantContext]]
PlatformOwnerDependency = Callable[[], Awaitable[PlatformOwnerPrincipal]]


class OrganizationProvisioner(Protocol):
    def provision(self, organization_name: str, owner_email: str) -> ProvisionedOwner: ...


class StaffInvitationIssuer(Protocol):
    def invite(
        self,
        context: TenantContext,
        *,
        email: str,
        role: UserRole,
    ) -> ProvisionedMember: ...


def build_organization_router(
    service: OrganizationService, *, principal_dependency: PrincipalDependency
) -> APIRouter:
    router = APIRouter(prefix="/organizations", tags=["organizations"])

    @router.post("/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
    async def create_member(
        request: CreateMemberRequest,
        principal: TenantContext = Depends(principal_dependency),
    ) -> MemberResponse:
        try:
            member = service.create_member(principal, email=request.email, role=request.role)
        except OrganizationPermissionDenied:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operation forbidden") from None
        return MemberResponse(user_id=member.user_id, email=member.email, role=member.role)

    return router


def build_staff_invitation_router(
    service: StaffInvitationIssuer,
    *,
    principal_dependency: PrincipalDependency,
) -> APIRouter:
    """Create pending staff accounts without giving callers control over the tenant."""
    router = APIRouter(prefix="/organizations", tags=["organizations"])

    @router.post(
        "/members/invitations",
        response_model=ProvisionedMemberResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_staff_invitation(
        request: CreateMemberRequest,
        principal: TenantContext = Depends(principal_dependency),
    ) -> ProvisionedMemberResponse:
        try:
            member = service.invite(principal, email=request.email, role=request.role)
        except OrganizationPermissionDenied:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operation forbidden") from None
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="request was not accepted") from None
        return ProvisionedMemberResponse(
            user_id=member.user_id,
            email=member.email,
            role=member.role,
            setup_token=member.setup_token,
        )

    return router


def build_workspace_organization_router(
    service: WorkspaceOrganizationService, *, principal_dependency: PrincipalDependency
) -> APIRouter:
    router = APIRouter(prefix="/workspace", tags=["workspace-organization"])

    @router.get("/organization", response_model=OrganizationProfileResponse)
    async def profile(principal: TenantContext = Depends(principal_dependency)) -> OrganizationProfileResponse:
        try:
            item = service.profile(principal)
        except LookupError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found") from None
        return OrganizationProfileResponse(organization_id=item.organization_id, name=item.name)

    @router.patch("/organization", response_model=OrganizationProfileResponse)
    async def rename(
        request: RenameOrganizationRequest,
        principal: TenantContext = Depends(principal_dependency),
    ) -> OrganizationProfileResponse:
        try:
            item = service.rename(principal, request.name)
        except OrganizationPermissionDenied:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operation forbidden") from None
        except LookupError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found") from None
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="request validation failed") from None
        return OrganizationProfileResponse(organization_id=item.organization_id, name=item.name)

    @router.get("/members", response_model=tuple[WorkspaceMemberResponse, ...])
    async def members(principal: TenantContext = Depends(principal_dependency)) -> tuple[WorkspaceMemberResponse, ...]:
        try:
            users = service.members(principal)
        except OrganizationPermissionDenied:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operation forbidden") from None
        return tuple(WorkspaceMemberResponse(user_id=user.id, email=user.email, role=user.role, is_active=user.disabled_at is None) for user in users)

    @router.post("/members/{user_id}/deactivate", response_model=WorkspaceMemberResponse)
    async def deactivate(user_id: UUID, principal: TenantContext = Depends(principal_dependency)) -> WorkspaceMemberResponse:
        try:
            user = service.deactivate(principal, user_id)
        except OrganizationPermissionDenied:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operation forbidden") from None
        return WorkspaceMemberResponse(user_id=user.id, email=user.email, role=user.role, is_active=False)

    return router


def build_platform_provisioning_router(
    service: OrganizationProvisioner,
    *,
    principal_dependency: PlatformOwnerDependency,
) -> APIRouter:
    """Expose initial tenant provisioning solely to the server-authenticated platform owner."""
    router = APIRouter(prefix="/platform", tags=["platform administration"])

    @router.post(
        "/organizations",
        response_model=ProvisionedOwnerResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_organization(
        request: CreateOrganizationRequest,
        _principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> ProvisionedOwnerResponse:
        try:
            owner = service.provision(request.organization_name, request.owner_email)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="request was not accepted") from None
        return ProvisionedOwnerResponse(
            organization_id=owner.organization_id,
            owner_email=owner.owner_email,
            setup_token=owner.setup_token,
        )

    return router
