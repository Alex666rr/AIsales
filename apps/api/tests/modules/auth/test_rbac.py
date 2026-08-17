"""Authentication and session contracts for Stage 1 roles."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.modules.auth.models import AuthUser, ServerSession
from app.modules.auth.passwords import hash_password
from app.modules.auth.service import (
    AuthenticationDenied,
    AuthService,
    SecondFactorRequired,
    SessionRevoked,
)
from app.modules.auth.session_auth import SessionAuthenticator
from app.modules.auth.totp import encrypt_totp_secret, totp_at
from app.modules.organizations.models import UserRole


ORG_ID = UUID("10000000-0000-0000-0000-000000000001")
OWNER_ID = UUID("20000000-0000-0000-0000-000000000001")
MANAGER_ID = UUID("30000000-0000-0000-0000-000000000001")
KEY = b"a" * 32
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class FakeAuthRepository:
    def __init__(self, users: list[AuthUser]) -> None:
        self.users = {user.email: user for user in users}
        self.sessions: dict[UUID, ServerSession] = {}

    def get_user_by_email(self, email: str) -> AuthUser | None:
        return self.users.get(email)

    def save_user(self, user: AuthUser) -> None:
        self.users[user.email] = user

    def save_session(self, session: ServerSession) -> None:
        self.sessions[session.id] = session

    def get_session(self, session_id: UUID) -> ServerSession | None:
        return self.sessions.get(session_id)


def owner() -> AuthUser:
    return AuthUser(
        id=OWNER_ID,
        organization_id=ORG_ID,
        email="owner@example.test",
        role=UserRole.COMPANY_OWNER,
        password_hash=hash_password("correct password"),
        encrypted_totp_secret=encrypt_totp_secret(b"12345678901234567890", KEY),
        recovery_code_hashes=(),
    )


def manager() -> AuthUser:
    return AuthUser(
        id=MANAGER_ID,
        organization_id=ORG_ID,
        email="manager@example.test",
        role=UserRole.MANAGER,
        password_hash=hash_password("correct password"),
        encrypted_totp_secret=None,
        recovery_code_hashes=(),
    )


def test_wrong_password_is_denied_without_creating_a_session():
    repository = FakeAuthRepository([manager()])
    service = AuthService(repository, encryption_key=KEY, now=lambda: NOW)

    with pytest.raises(AuthenticationDenied):
        service.login(email="manager@example.test", password="wrong password")

    assert repository.sessions == {}


def test_company_owner_needs_totp_before_a_server_session_is_issued():
    repository = FakeAuthRepository([owner()])
    service = AuthService(repository, encryption_key=KEY, now=lambda: NOW)

    with pytest.raises(SecondFactorRequired):
        service.login(email="owner@example.test", password="correct password")

    assert repository.sessions == {}
    session = service.login(
        email="owner@example.test",
        password="correct password",
        totp_code=totp_at(b"12345678901234567890", NOW),
    )
    assert session.user_id == OWNER_ID
    assert session.mfa_verified is True


def test_revoked_session_is_not_accepted_as_an_authenticated_principal():
    repository = FakeAuthRepository([manager()])
    service = AuthService(repository, encryption_key=KEY, now=lambda: NOW)
    session = service.login(email="manager@example.test", password="correct password")

    service.revoke_session(session.id)

    with pytest.raises(SessionRevoked):
        service.require_session(session.id)


def test_cookie_session_is_issued_as_a_trusted_tenant_context():
    repository = FakeAuthRepository([manager()])
    service = AuthService(repository, encryption_key=KEY, now=lambda: NOW)
    session = service.login(email="manager@example.test", password="correct password")

    context = SessionAuthenticator(service).from_session_id(str(session.id))

    assert context.organization_id == ORG_ID
    assert context.actor_id == MANAGER_ID
    assert context.roles == frozenset({"manager"})


def test_server_session_has_expiry_and_activity_timestamps():
    service = AuthService(FakeAuthRepository([manager()]), encryption_key=KEY, now=lambda: NOW)

    session = service.login(email="manager@example.test", password="correct password")

    assert session.last_active_at == NOW
    assert session.expires_at > NOW
