"""Convert a validated server-side session into trusted application context."""

from __future__ import annotations

from uuid import UUID

from typing import Annotated

from fastapi import Cookie, HTTPException, status

from app.modules.auth.service import AuthService, SessionRevoked
from app.modules.shared.commands import TenantContext


class SessionAuthenticator:
    """The request body never chooses organization, user, or roles."""

    def __init__(self, service: AuthService) -> None:
        self._service = service

    def from_session_id(self, value: str) -> TenantContext:
        try:
            session = self._service.require_session(UUID(value))
        except (SessionRevoked, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            ) from None
        return TenantContext(
            organization_id=session.organization_id,
            actor_id=session.user_id,
            roles=session.roles,
        )

    async def __call__(
        self,
        session_id: Annotated[str | None, Cookie(alias="aisales_session")] = None,
    ) -> TenantContext:
        if session_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
        return self.from_session_id(session_id)
