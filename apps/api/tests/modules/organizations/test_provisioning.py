"""Durable setup-token contracts for the first company owner."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from app.modules.auth.persistence import AUTH_METADATA, SqlAlchemyAuthRepository
import pytest

from app.modules.auth.passwords import verify_password, verify_recovery_code
from app.modules.organizations.models import UserRole
from app.modules.organizations.provisioning import ProvisioningService


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
KEY = b"p" * 32


def test_provisioning_persists_a_non_reversible_one_time_setup_token(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'provisioning.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    service = ProvisioningService(repository, encryption_key=KEY, now=lambda: NOW)

    try:
        result = service.provision("Acme", "owner@example.test")
        user = repository.get_user_by_email("owner@example.test")
        invitation = repository.get_setup_invitation(user.id if user is not None else None)

        assert user is not None
        assert user.organization_id == result.organization_id
        assert user.role is UserRole.COMPANY_OWNER
        assert user.password_hash is None
        assert invitation is not None
        assert invitation.user_id == user.id
        assert invitation.consumed_at is None
        assert invitation.token_hash != result.setup_token
        assert verify_recovery_code(result.setup_token, invitation.token_hash)
        assert "setup_token" not in repr(result)
    finally:
        engine.dispose()


def test_setup_token_sets_a_password_exactly_once(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'activation.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    service = ProvisioningService(repository, encryption_key=KEY, now=lambda: NOW)

    try:
        invitation = service.provision("Acme", "owner@example.test")

        service.activate_setup_token(invitation.setup_token, password="a secure password")

        user = repository.get_user_by_email("owner@example.test")
        stored_invitation = repository.get_setup_invitation(user.id if user is not None else None)
        assert user is not None
        assert user.password_hash is not None
        assert verify_password("a secure password", user.password_hash)
        assert stored_invitation is not None
        assert stored_invitation.consumed_at == NOW

        with pytest.raises(PermissionError):
            service.activate_setup_token(invitation.setup_token, password="another password")
    finally:
        engine.dispose()


def test_expired_setup_token_cannot_set_a_password(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'expired-activation.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    issuer = ProvisioningService(repository, encryption_key=KEY, now=lambda: NOW)
    expired_consumer = ProvisioningService(
        repository,
        encryption_key=KEY,
        now=lambda: NOW + timedelta(hours=49),
    )

    try:
        invitation = issuer.provision("Acme", "owner@example.test")

        with pytest.raises(PermissionError):
            expired_consumer.activate_setup_token(invitation.setup_token, password="a secure password")

        user = repository.get_user_by_email("owner@example.test")
        assert user is not None
        assert user.password_hash is None
    finally:
        engine.dispose()


def test_owner_email_is_unique_across_organizations(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'unique-email.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    service = ProvisioningService(repository, encryption_key=KEY, now=lambda: NOW)

    try:
        service.provision("Acme", "owner@example.test")

        with pytest.raises(ValueError, match="owner email already exists"):
            service.provision("Beta", "owner@example.test")
    finally:
        engine.dispose()


def test_setup_activation_returns_a_one_time_totp_enrollment_uri(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'setup-totp.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    service = ProvisioningService(repository, encryption_key=KEY, now=lambda: NOW)

    try:
        invitation = service.provision("Acme", "owner@example.test")

        pending = service.activate_setup_token(invitation.setup_token, password="a secure password")

        stored = repository.get_totp_enrollment(pending.enrollment_id)
        assert pending.totp_uri.startswith("otpauth://totp/AIsales:")
        assert pending.enrollment_token not in pending.totp_uri
        assert stored is not None
        assert verify_recovery_code(pending.enrollment_token, stored.token_hash)
        assert "enrollment_token" not in repr(pending)
        assert "totp_uri" not in repr(pending)
    finally:
        engine.dispose()
