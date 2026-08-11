"""Authenticated, redacted HTTP routes for phone connection attempts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.policy.models import PlatformOwnerPrincipal

from .models import AttemptView


class PhoneStartRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phone: str = Field(pattern=r"^\+[1-9]\d{6,14}$")


class PhoneCodeRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^\d{4,8}$")


class PhonePasswordRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    password: str = Field(min_length=1, max_length=512)


class PhoneAttemptRoutes(Protocol):
    async def start_phone(self, owner: PlatformOwnerPrincipal, phone: str) -> AttemptView: ...

    async def submit_code(
        self,
        owner: PlatformOwnerPrincipal,
        attempt_id: UUID,
        code: str,
    ) -> AttemptView: ...

    async def submit_password(
        self,
        owner: PlatformOwnerPrincipal,
        attempt_id: UUID,
        password: str,
    ) -> AttemptView: ...


PrincipalDependency = Callable[[], Awaitable[PlatformOwnerPrincipal]]


def build_connection_router(
    attempts: PhoneAttemptRoutes,
    *,
    principal_dependency: PrincipalDependency,
) -> APIRouter:
    """Build routes that receive identity exclusively from server auth."""
    router = APIRouter(prefix="/telegram/connections", tags=["telegram-connections"])

    @router.post("/phone/start", response_model=AttemptView, status_code=status.HTTP_201_CREATED)
    async def start_phone(
        request: PhoneStartRequest,
        principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> AttemptView:
        return await _safe_call(attempts.start_phone(principal, request.phone))

    @router.post("/{attempt_id}/phone/confirm", response_model=AttemptView)
    async def confirm_phone(
        attempt_id: UUID,
        request: PhoneCodeRequest,
        principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> AttemptView:
        return await _safe_call(attempts.submit_code(principal, attempt_id, request.code))

    @router.post("/{attempt_id}/phone/password", response_model=AttemptView)
    async def confirm_password(
        attempt_id: UUID,
        request: PhonePasswordRequest,
        principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> AttemptView:
        return await _safe_call(attempts.submit_password(principal, attempt_id, request.password))

    return router


async def _safe_call(operation: Awaitable[AttemptView]) -> AttemptView:
    try:
        return await operation
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="telegram connection unavailable",
        ) from None
