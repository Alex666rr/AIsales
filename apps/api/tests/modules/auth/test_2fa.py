"""Second-factor and recovery-code contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.modules.auth.models import AuthUser, ServerSession
from app.modules.auth.passwords import hash_password, hash_recovery_code
from app.modules.auth.service import AuthenticationDenied, AuthService, SecondFactorRequired
from app.modules.auth.totp import encrypt_totp_secret
from app.modules.organizations.models import UserRole


ORG_ID = UUID("40000000-0000-0000-0000-000000000001")
OWNER_ID = UUID("50000000-0000-0000-0000-000000000001")
KEY = b"b" * 32
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class FakeAuthRepository:
    def __init__(self, user: AuthUser) -> None:
        self.user = user
        self.sessions: dict[UUID, ServerSession] = {}

    def get_user_by_email(self, email: str) -> AuthUser | None:
        return self.user if self.user.email == email else None

    def save_user(self, user: AuthUser) -> None:
        self.user = user

    def save_session(self, session: ServerSession) -> None:
        self.sessions[session.id] = session

    def get_session(self, session_id: UUID) -> ServerSession | None:
        return self.sessions.get(session_id)


def test_recovery_code_is_consumed_after_successful_privileged_login():
    recovery_code = "repair-1234"
    user = AuthUser(
        id=OWNER_ID,
        organization_id=ORG_ID,
        email="owner@example.test",
        role=UserRole.COMPANY_OWNER,
        password_hash=hash_password("correct password"),
        encrypted_totp_secret=None,
        recovery_code_hashes=(hash_recovery_code(recovery_code),),
    )
    repository = FakeAuthRepository(user)
    service = AuthService(repository, encryption_key=KEY, now=lambda: NOW)

    session = service.login(
        email=user.email,
        password="correct password",
        recovery_code=recovery_code,
    )
    assert session.mfa_verified is True
    assert repository.user.recovery_code_hashes == ()

    with pytest.raises(SecondFactorRequired):
        service.login(email=user.email, password="correct password", recovery_code=recovery_code)


def test_unreadable_totp_secret_fails_closed_without_crypto_detail():
    user = AuthUser(
        id=OWNER_ID,
        organization_id=ORG_ID,
        email="owner@example.test",
        role=UserRole.COMPANY_OWNER,
        password_hash=hash_password("correct password"),
        encrypted_totp_secret=encrypt_totp_secret(b"12345678901234567890", b"c" * 32),
        recovery_code_hashes=(),
    )
    service = AuthService(FakeAuthRepository(user), encryption_key=KEY, now=lambda: NOW)

    with pytest.raises(AuthenticationDenied) as error:
        service.login(email=user.email, password="correct password", totp_code="000000")

    assert "InvalidTag" not in str(error.value)
