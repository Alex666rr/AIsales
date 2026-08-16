"""Credential-safe HTTP endpoints for server-side authentication sessions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.auth.service import AuthenticationDenied, AuthService


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

    return router
