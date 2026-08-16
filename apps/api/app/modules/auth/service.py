"""Password, second-factor, and revocable server-session service."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Callable, Protocol
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag

from app.modules.auth.models import AuthUser, ServerSession
from app.modules.auth.passwords import verify_password, verify_recovery_code
from app.modules.auth.totp import decrypt_totp_secret, verify_totp
from app.modules.organizations.models import UserRole


class AuthRepository(Protocol):
    def get_user_by_email(self, email: str) -> AuthUser | None: ...

    def save_user(self, user: AuthUser) -> None: ...

    def save_session(self, session: ServerSession) -> None: ...

    def get_session(self, session_id: UUID) -> ServerSession | None: ...


class AuthenticationDenied(PermissionError):
    """Password or credential verification did not succeed."""


class SecondFactorRequired(AuthenticationDenied):
    """A privileged identity needs TOTP or a remaining recovery code."""


class SessionRevoked(AuthenticationDenied):
    """A server-side session was revoked or is unavailable."""


class AuthService:
    """Issues a server session only after all role-required factors succeed."""

    _PRIVILEGED_ROLES = {
        UserRole.PLATFORM_OWNER,
        UserRole.COMPANY_OWNER,
        UserRole.ADMINISTRATOR,
    }

    def __init__(
        self,
        repository: AuthRepository,
        *,
        encryption_key: bytes,
        now: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._encryption_key = encryption_key
        self._now = now

    def login(
        self,
        *,
        email: str,
        password: str,
        totp_code: str | None = None,
        recovery_code: str | None = None,
    ) -> ServerSession:
        user = self._repository.get_user_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise AuthenticationDenied("authentication was not accepted")

        mfa_verified = False
        if user.role in self._PRIVILEGED_ROLES:
            user = self._verify_second_factor(user, totp_code, recovery_code)
            mfa_verified = True

        session = ServerSession(
            id=uuid4(),
            user_id=user.id,
            organization_id=user.organization_id,
            roles=frozenset({user.role.value}),
            mfa_verified=mfa_verified,
            issued_at=self._now(),
        )
        self._repository.save_session(session)
        return session

    def revoke_session(self, session_id: UUID) -> None:
        session = self._repository.get_session(session_id)
        if session is not None and session.revoked_at is None:
            self._repository.save_session(replace(session, revoked_at=self._now()))

    def require_session(self, session_id: UUID) -> ServerSession:
        session = self._repository.get_session(session_id)
        if session is None or session.revoked_at is not None:
            raise SessionRevoked("session was not accepted")
        return session

    def _verify_second_factor(
        self,
        user: AuthUser,
        totp_code: str | None,
        recovery_code: str | None,
    ) -> AuthUser:
        if totp_code is not None and user.encrypted_totp_secret is not None:
            try:
                secret = decrypt_totp_secret(user.encrypted_totp_secret, self._encryption_key)
            except (InvalidTag, ValueError) as exc:
                raise AuthenticationDenied("authentication was not accepted") from exc
            if verify_totp(secret, totp_code, self._now()):
                return user

        if recovery_code is not None:
            for stored_hash in user.recovery_code_hashes:
                if verify_recovery_code(recovery_code, stored_hash):
                    updated_user = replace(
                        user,
                        recovery_code_hashes=tuple(
                            value for value in user.recovery_code_hashes if value != stored_hash
                        ),
                    )
                    self._repository.save_user(updated_user)
                    return updated_user

        raise SecondFactorRequired("second factor is required")
