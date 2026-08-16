"""Tenant-safe organization administration HTTP contracts."""

from __future__ import annotations

from uuid import UUID
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.organizations.models import UserRole
from app.modules.organizations.service import OrganizationPermissionDenied, OrganizationService
from app.modules.shared.commands import TenantContext


class CreateMemberRequest(BaseModel):
    """Target organization is deliberately absent; it comes from authentication context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: UserRole


class MemberResponse(BaseModel):
    """Safe membership view; password hashes and MFA material never leave storage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID
    email: str
    role: UserRole


PrincipalDependency = Callable[[], Awaitable[TenantContext]]


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
