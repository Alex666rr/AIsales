"""Second-factor and recovery-code contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import create_engine

from app.modules.auth import models
from app.modules.auth.models import AuthUser, ServerSession, TotpEnrollmentChallenge
from app.modules.auth.passwords import hash_password, hash_recovery_code, verify_recovery_code
from app.modules.auth.persistence import AUTH_METADATA, SqlAlchemyAuthRepository
from app.modules.auth.service import AuthenticationDenied, AuthService, SecondFactorRequired
from app.modules.auth.totp import encrypt_totp_secret, totp_at
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


def test_totp_enrollment_challenge_persists_only_hashed_token_and_encrypted_secret(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'totp-enrollment.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    user = AuthUser(
        id=OWNER_ID,
        organization_id=ORG_ID,
        email="owner@example.test",
        role=UserRole.COMPANY_OWNER,
        password_hash=hash_password("correct password"),
        encrypted_totp_secret=None,
        recovery_code_hashes=(),
    )
    challenge_type = models.TotpEnrollmentChallenge
    raw_token = "enrollment-token-SENTINEL"
    challenge = challenge_type(
        id=UUID("60000000-0000-0000-0000-000000000001"),
        user_id=OWNER_ID,
        token_hash=hash_recovery_code(raw_token),
        encrypted_secret=encrypt_totp_secret(b"12345678901234567890", KEY),
        expires_at=NOW + timedelta(minutes=10),
    )

    try:
        repository.create_organization(ORG_ID, "Acme")
        repository.save_user(user)
        repository.create_totp_enrollment(challenge)
        stored = repository.get_totp_enrollment(challenge.id)

        assert stored == challenge
        assert raw_token not in repr(stored)
        assert raw_token not in stored.token_hash
        assert stored.encrypted_secret != b"12345678901234567890"
    finally:
        engine.dispose()


def test_totp_enrollment_confirmation_stores_secret_and_returns_one_time_recovery_codes(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'totp-confirmation.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    user = AuthUser(
        id=OWNER_ID,
        organization_id=ORG_ID,
        email="owner@example.test",
        role=UserRole.COMPANY_OWNER,
        password_hash=hash_password("correct password"),
        encrypted_totp_secret=None,
        recovery_code_hashes=(),
    )
    secret = b"12345678901234567890"
    token = "60000000-0000-0000-0000-000000000002.confirmation-SENTINEL"
    challenge = TotpEnrollmentChallenge(
        id=UUID("60000000-0000-0000-0000-000000000002"),
        user_id=OWNER_ID,
        token_hash=hash_recovery_code(token),
        encrypted_secret=encrypt_totp_secret(secret, KEY),
        expires_at=NOW + timedelta(minutes=10),
    )

    try:
        repository.create_organization(ORG_ID, "Acme")
        repository.save_user(user)
        repository.create_totp_enrollment(challenge)
        service = AuthService(repository, encryption_key=KEY, now=lambda: NOW)

        recovery_codes = service.confirm_totp_enrollment(
            enrollment_token=token,
            code=totp_at(secret, NOW),
        )

        stored_user = repository.get_user_by_id(OWNER_ID)
        stored_challenge = repository.get_totp_enrollment(challenge.id)
        assert stored_user is not None
        assert stored_user.encrypted_totp_secret == challenge.encrypted_secret
        assert len(recovery_codes) == 10
        assert all(
            any(verify_recovery_code(code, value) for value in stored_user.recovery_code_hashes)
            for code in recovery_codes
        )
        assert stored_challenge is not None
        assert stored_challenge.consumed_at == NOW
        assert "SENTINEL" not in repr(recovery_codes)

        with pytest.raises(AuthenticationDenied):
            service.confirm_totp_enrollment(enrollment_token=token, code=totp_at(secret, NOW))
    finally:
        engine.dispose()


def test_totp_enrollment_confirmation_rejects_wrong_code_without_consuming(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'totp-denial.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    user = AuthUser(
        id=OWNER_ID,
        organization_id=ORG_ID,
        email="owner@example.test",
        role=UserRole.COMPANY_OWNER,
        password_hash=hash_password("correct password"),
        encrypted_totp_secret=None,
        recovery_code_hashes=(),
    )
    secret = b"12345678901234567890"
    token = "60000000-0000-0000-0000-000000000003.denial-SENTINEL"
    challenge = TotpEnrollmentChallenge(
        id=UUID("60000000-0000-0000-0000-000000000003"),
        user_id=OWNER_ID,
        token_hash=hash_recovery_code(token),
        encrypted_secret=encrypt_totp_secret(secret, KEY),
        expires_at=NOW + timedelta(minutes=10),
    )

    try:
        repository.create_organization(ORG_ID, "Acme")
        repository.save_user(user)
        repository.create_totp_enrollment(challenge)
        service = AuthService(repository, encryption_key=KEY, now=lambda: NOW)

        assert not repository.consume_totp_enrollment(
            challenge=challenge,
            enrollment_token="counterfeit-token",
            recovery_code_hashes=(hash_recovery_code("unused"),),
            now=NOW,
        )

        with pytest.raises(AuthenticationDenied):
            service.confirm_totp_enrollment(enrollment_token=token, code="000000")

        stored = repository.get_totp_enrollment(challenge.id)
        updated_user = repository.get_user_by_id(OWNER_ID)
        assert stored is not None and stored.consumed_at is None
        assert updated_user is not None and updated_user.encrypted_totp_secret is None
    finally:
        engine.dispose()


def test_totp_enrollment_confirmation_rejects_an_expired_challenge(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'totp-expired.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    user = AuthUser(
        id=OWNER_ID,
        organization_id=ORG_ID,
        email="owner@example.test",
        role=UserRole.COMPANY_OWNER,
        password_hash=hash_password("correct password"),
        encrypted_totp_secret=None,
        recovery_code_hashes=(),
    )
    secret = b"12345678901234567890"
    token = "60000000-0000-0000-0000-000000000004.expired-SENTINEL"
    challenge = TotpEnrollmentChallenge(
        id=UUID("60000000-0000-0000-0000-000000000004"),
        user_id=OWNER_ID,
        token_hash=hash_recovery_code(token),
        encrypted_secret=encrypt_totp_secret(secret, KEY),
        expires_at=NOW - timedelta(seconds=1),
    )

    try:
        repository.create_organization(ORG_ID, "Acme")
        repository.save_user(user)
        repository.create_totp_enrollment(challenge)

        with pytest.raises(AuthenticationDenied):
            AuthService(repository, encryption_key=KEY, now=lambda: NOW).confirm_totp_enrollment(
                enrollment_token=token,
                code=totp_at(secret, NOW),
            )

        stored = repository.get_totp_enrollment(challenge.id)
        assert stored is not None and stored.consumed_at is None
    finally:
        engine.dispose()
