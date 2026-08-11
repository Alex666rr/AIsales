"""Fail-closed administrative routes for immutable approval history."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from .models import ApprovalGrantRequest, PlatformOwnerPrincipal
from .repository import ApprovalWriteRejected
from .service import ApprovalAdministrationService, PolicyAuthorizationError


class ApprovalMutationResponse(BaseModel):
    """Minimal public acknowledgement without evidence or audit contents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: UUID
    status: str


PrincipalDependency = Callable[[], Awaitable[PlatformOwnerPrincipal]]


def build_policy_router(
    administration: ApprovalAdministrationService,
    *,
    principal_dependency: PrincipalDependency,
) -> APIRouter:
    """Build routes only when composition supplies trusted server authentication."""
    router = APIRouter(prefix="/policy", tags=["policy"])

    @router.post(
        "/ai-approvals",
        response_model=ApprovalMutationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_approval(
        request: ApprovalGrantRequest,
        principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> ApprovalMutationResponse:
        try:
            record = await administration.create(request, principal)
        except PolicyAuthorizationError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operation forbidden") from None
        except ApprovalWriteRejected:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="approval request rejected") from None
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="approval operation unavailable",
            ) from None
        return ApprovalMutationResponse(approval_id=record.approval_id, status="created")

    @router.post("/ai-approvals/{approval_id}/revocations", response_model=ApprovalMutationResponse)
    async def revoke_approval(
        approval_id: UUID,
        principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> ApprovalMutationResponse:
        try:
            record = await administration.revoke(approval_id, principal)
        except PolicyAuthorizationError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operation forbidden") from None
        except ApprovalWriteRejected:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found") from None
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="approval operation unavailable",
            ) from None
        return ApprovalMutationResponse(approval_id=record.approval_id, status="revoked")

    return router
