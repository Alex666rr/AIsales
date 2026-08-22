"""Authenticated, redacted HTTP routes for phone connection attempts."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.policy.models import PlatformOwnerPrincipal
from app.modules.shared.commands import TenantContext

from .models import AttemptView, ConnectionStatusView, QrStartView, TdataConnectionView
from .tdata_ticket import TdataTicketRegistry


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

    async def start_qr(self, owner: PlatformOwnerPrincipal) -> QrStartView: ...

    async def qr_status(
        self,
        owner: PlatformOwnerPrincipal,
        attempt_id: UUID,
    ) -> AttemptView: ...


PrincipalDependency = Callable[[], Awaitable[PlatformOwnerPrincipal]]
WorkspacePrincipalDependency = Callable[[], Awaitable[TenantContext]]


class TdataTicketView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: UUID
    expires_at: datetime
    public_key: str


class TdataHandoffRequest(BaseModel):
    """Base64url envelope produced locally after tdata was converted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_public_key: str = Field(min_length=1, max_length=128, repr=False)
    nonce: str = Field(min_length=1, max_length=64, repr=False)
    ciphertext: str = Field(min_length=1, max_length=16_384, repr=False)


class TdataHandoffRoutes(Protocol):
    async def accept(
        self,
        owner: PlatformOwnerPrincipal,
        ticket_id: UUID,
        client_public_key: bytes,
        nonce: bytes,
        ciphertext: bytes,
    ) -> TdataConnectionView: ...


class ConnectionStatusRoutes(Protocol):
    async def get(
        self, owner: PlatformOwnerPrincipal, account_id: UUID
    ) -> ConnectionStatusView: ...


def build_connection_router(
    attempts: PhoneAttemptRoutes,
    *,
    principal_dependency: PrincipalDependency,
    statuses: ConnectionStatusRoutes | None = None,
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

    @router.post("/qr/start", response_model=QrStartView, status_code=status.HTTP_201_CREATED)
    async def start_qr(
        principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> QrStartView:
        return await _safe_call(attempts.start_qr(principal))

    @router.get("/{attempt_id}/qr/status", response_model=AttemptView)
    async def qr_status(
        attempt_id: UUID,
        principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> AttemptView:
        return await _safe_call(attempts.qr_status(principal, attempt_id))

    @router.get("/{account_id}", response_model=ConnectionStatusView)
    async def connection_status(
        account_id: UUID,
        principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> ConnectionStatusView:
        if statuses is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="telegram connection unavailable")
        try:
            return await statuses.get(principal, account_id)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="telegram connection not found") from None
        except Exception:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="telegram connection unavailable") from None

    return router


class WorkspacePhoneAttemptRoutes(Protocol):
    async def start_phone(self, principal: TenantContext, phone: str) -> AttemptView: ...

    async def submit_code(
        self, principal: TenantContext, attempt_id: UUID, code: str
    ) -> AttemptView: ...

    async def submit_password(
        self, principal: TenantContext, attempt_id: UUID, password: str
    ) -> AttemptView: ...

    async def start_qr(self, principal: TenantContext) -> QrStartView: ...

    async def qr_status(
        self, principal: TenantContext, attempt_id: UUID
    ) -> AttemptView: ...


class WorkspaceConnectionStatusRoutes(Protocol):
    async def get(
        self, principal: TenantContext, account_id: UUID
    ) -> ConnectionStatusView: ...


def build_workspace_connection_router(
    attempts: WorkspacePhoneAttemptRoutes,
    *,
    principal_dependency: WorkspacePrincipalDependency,
    statuses: WorkspaceConnectionStatusRoutes | None = None,
) -> APIRouter:
    """Build session-authenticated workspace routes; never expose local tdata handoff."""
    router = APIRouter(
        prefix="/workspace/telegram/connections", tags=["workspace-telegram-connections"]
    )

    @router.post("/phone/start", response_model=AttemptView, status_code=status.HTTP_201_CREATED)
    async def start_phone(
        request: PhoneStartRequest,
        principal: TenantContext = Depends(principal_dependency),
    ) -> AttemptView:
        return await _safe_call(attempts.start_phone(principal, request.phone))

    @router.post("/{attempt_id}/phone/confirm", response_model=AttemptView)
    async def confirm_phone(
        attempt_id: UUID,
        request: PhoneCodeRequest,
        principal: TenantContext = Depends(principal_dependency),
    ) -> AttemptView:
        return await _safe_call(attempts.submit_code(principal, attempt_id, request.code))

    @router.post("/{attempt_id}/phone/password", response_model=AttemptView)
    async def confirm_password(
        attempt_id: UUID,
        request: PhonePasswordRequest,
        principal: TenantContext = Depends(principal_dependency),
    ) -> AttemptView:
        return await _safe_call(attempts.submit_password(principal, attempt_id, request.password))

    @router.post("/qr/start", response_model=QrStartView, status_code=status.HTTP_201_CREATED)
    async def start_qr(
        principal: TenantContext = Depends(principal_dependency),
    ) -> QrStartView:
        return await _safe_call(attempts.start_qr(principal))

    @router.get("/{attempt_id}/qr/status", response_model=AttemptView)
    async def qr_status(
        attempt_id: UUID,
        principal: TenantContext = Depends(principal_dependency),
    ) -> AttemptView:
        return await _safe_call(attempts.qr_status(principal, attempt_id))

    @router.get("/{account_id}", response_model=ConnectionStatusView)
    async def connection_status(
        account_id: UUID,
        principal: TenantContext = Depends(principal_dependency),
    ) -> ConnectionStatusView:
        if statuses is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="telegram connection unavailable",
            )
        try:
            return await statuses.get(principal, account_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="telegram connection not found",
            ) from None
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="telegram connection unavailable",
            ) from None

    return router


def build_tdata_ticket_router(
    tickets: TdataTicketRegistry,
    *,
    principal_dependency: PrincipalDependency,
    handoffs: TdataHandoffRoutes | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/telegram/connections/tdata", tags=["telegram-connections"])

    @router.post("/tickets", response_model=TdataTicketView, status_code=status.HTTP_201_CREATED)
    async def issue_ticket(
        principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> TdataTicketView:
        ticket = await tickets.issue(principal.principal_id)
        return TdataTicketView(
            ticket_id=ticket.ticket_id,
            expires_at=ticket.expires_at,
            public_key=ticket.public_key,
        )

    @router.post("/tickets/{ticket_id}/handoff", response_model=TdataConnectionView, status_code=status.HTTP_201_CREATED)
    async def accept_handoff(
        ticket_id: UUID,
        request: TdataHandoffRequest,
        principal: PlatformOwnerPrincipal = Depends(principal_dependency),
    ) -> TdataConnectionView:
        if handoffs is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="telegram connection unavailable")
        try:
            return await handoffs.accept(
                principal,
                ticket_id,
                _decode_base64url(request.client_public_key),
                _decode_base64url(request.nonce),
                _decode_base64url(request.ciphertext),
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tdata handoff rejected",
            ) from None

    return router


def _decode_base64url(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except Exception:
        raise ValueError("invalid tdata handoff envelope") from None


_RouteView = TypeVar("_RouteView", bound=AttemptView)


async def _safe_call(operation: Awaitable[_RouteView]) -> _RouteView:
    try:
        return await operation
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="telegram connection unavailable",
        ) from None
