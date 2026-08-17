"""Durable SQL storage contracts for Stage 1 authentication."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import create_engine

from app.modules.auth.models import AuthUser, ServerSession
from app.modules.auth.persistence import AUTH_METADATA, SqlAlchemyAuthRepository
from app.modules.organizations.models import UserRole


ORG_ID = UUID("80000000-0000-0000-0000-000000000001")
USER_ID = UUID("90000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def test_auth_repository_persists_one_way_credentials_and_revocation(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'auth.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    try:
        repository.create_organization(ORG_ID, "Acme")
        repository.save_user(
            AuthUser(
                id=USER_ID,
                organization_id=ORG_ID,
                email="owner@example.test",
                role=UserRole.COMPANY_OWNER,
                password_hash="scrypt-v1$stored-only",
                encrypted_totp_secret="ciphertext-only",
                recovery_code_hashes=("one-way-code",),
            )
        )

        stored_user = repository.get_user_by_email("owner@example.test")
        assert stored_user is not None
        assert stored_user.password_hash == "scrypt-v1$stored-only"
        assert stored_user.encrypted_totp_secret == "ciphertext-only"

        session = ServerSession(
            id=uuid4(),
            user_id=USER_ID,
            organization_id=ORG_ID,
            roles=frozenset({"company_owner"}),
            mfa_verified=True,
            issued_at=NOW,
            last_active_at=NOW,
            expires_at=NOW,
            revoked_at=NOW,
        )
        repository.save_session(session)

        assert repository.get_session(session.id) == session
    finally:
        engine.dispose()
