"""Credential-safe HTTP endpoints for server-side authentication sessions."""

from __future__ import annotations

from typing import Protocol
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.auth.service import AuthenticationDenied, AuthService
from app.modules.organizations.provisioning import PendingTotpEnrollment


SESSION_COOKIE_NAME = "aisales_session"


class LoginRequest(BaseModel):
    """Credentials are accepted only in a JSON body and excluded from representations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512, repr=False)
    totp_code: str | None = Field(default=None, pattern=r"^\d{6}$", repr=False)
    recovery_code: str | None = Field(default=None, min_length=1, max_length=256, repr=False)


class LoginResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mfa_verified: bool


class SetupRequest(BaseModel):
    """Initial password setup material; neither value may appear in representations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    setup_token: str = Field(min_length=1, max_length=1024, repr=False)
    password: str = Field(min_length=12, max_length=512, repr=False)


class SetupResponse(BaseModel):
    """Scan-only TOTP material, returned only after a setup token is consumed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enrollment_token: str = Field(repr=False)
    totp_uri: str = Field(repr=False)


class TotpConfirmationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enrollment_token: str = Field(min_length=1, max_length=1024, repr=False)
    code: str = Field(pattern=r"^\d{6}$", repr=False)


class RecoveryCodesResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recovery_codes: list[str] = Field(repr=False)


class SetupActivator(Protocol):
    def activate_setup_token(self, setup_token: str, *, password: str) -> PendingTotpEnrollment: ...


def build_auth_router(service: AuthService) -> APIRouter:
    """Build auth routes with session IDs confined to secure HttpOnly cookies."""
    router = APIRouter(prefix="/auth", tags=["authentication"])

    @router.post("/login", response_model=LoginResponse)
    async def login(request: LoginRequest, response: Response) -> LoginResponse:
        try:
            session = service.login(
                email=request.email,
                password=request.password,
                totp_code=request.totp_code,
                recovery_code=request.recovery_code,
            )
        except AuthenticationDenied:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication was not accepted",
            ) from None
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=str(session.id),
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return LoginResponse(mfa_verified=session.mfa_verified)

    @router.post("/totp/confirm", response_model=RecoveryCodesResponse)
    async def confirm_totp(request: TotpConfirmationRequest) -> RecoveryCodesResponse:
        try:
            codes = service.confirm_totp_enrollment(
                enrollment_token=request.enrollment_token,
                code=request.code,
            )
        except AuthenticationDenied:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication was not accepted",
            ) from None
        return RecoveryCodesResponse(recovery_codes=list(codes))

    return router


def build_setup_router(activator: SetupActivator) -> APIRouter:
    """Build the public one-time initial password setup endpoint."""
    router = APIRouter(prefix="/auth", tags=["authentication"])

    @router.post("/setup", response_model=SetupResponse)
    async def complete_setup(request: SetupRequest) -> SetupResponse:
        try:
            pending = activator.activate_setup_token(request.setup_token, password=request.password)
        except PermissionError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="setup token was not accepted",
            ) from None
        return SetupResponse(
            enrollment_token=pending.enrollment_token,
            totp_uri=pending.totp_uri,
        )

    return router
