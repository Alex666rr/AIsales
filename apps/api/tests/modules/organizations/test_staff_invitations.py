"""One-time staff setup invitations issued by a tenant owner."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine

from app.modules.auth.passwords import verify_recovery_code
from app.modules.auth.persistence import AUTH_METADATA, SqlAlchemyAuthRepository
from app.modules.organizations.models import UserRole
from app.modules.organizations.provisioning import ProvisioningService, StaffInvitationService
from app.modules.organizations.service import OrganizationPermissionDenied
from app.modules.shared.commands import TenantContext


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
KEY = b"p" * 32
ORG_ID = UUID("10000000-0000-0000-0000-000000000001")
OWNER_ID = UUID("20000000-0000-0000-0000-000000000001")


def _owner_context() -> TenantContext:
    return TenantContext(
        organization_id=ORG_ID,
        actor_id=OWNER_ID,
        roles=frozenset({UserRole.COMPANY_OWNER.value}),
    )


def test_company_owner_issues_a_single_use_setup_token_for_a_manager(tmp_path) -> None:
    """The raw token is returned once but the database only retains its hash."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'staff-invitation.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    owners = ProvisioningService(repository, encryption_key=KEY, now=lambda: NOW)
    staff = StaffInvitationService(repository, now=lambda: NOW)
    try:
        owner = owners.provision("Acme", "owner@example.test")
        owner_user = repository.get_user_by_email(owner.owner_email)
        assert owner_user is not None
        context = TenantContext(
            organization_id=owner.organization_id,
            actor_id=owner_user.id,
            roles=frozenset({UserRole.COMPANY_OWNER.value}),
        )

        invitation = staff.invite(
            context,
            email="manager@example.test",
            role=UserRole.MANAGER,
        )

        user = repository.get_user_by_email("manager@example.test")
        assert user is not None
        stored = repository.get_setup_invitation(user.id)
        assert user.organization_id == owner.organization_id
        assert user.role is UserRole.MANAGER
        assert user.password_hash is None
        assert stored is not None
        assert stored.token_hash != invitation.setup_token
        assert verify_recovery_code(invitation.setup_token, stored.token_hash)
        assert "setup_token" not in repr(invitation)
    finally:
        engine.dispose()


def test_manager_cannot_issue_staff_invitation(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'staff-invitation-denied.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    staff = StaffInvitationService(repository, now=lambda: NOW)
    try:
        with pytest.raises(OrganizationPermissionDenied):
            staff.invite(
                TenantContext(organization_id=ORG_ID, actor_id=OWNER_ID, roles=frozenset({"manager"})),
                email="other@example.test",
                role=UserRole.MANAGER,
            )
    finally:
        engine.dispose()


def test_staff_invitation_cannot_create_an_owner_role(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'staff-invitation-role.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    staff = StaffInvitationService(repository, now=lambda: NOW)
    try:
        with pytest.raises(OrganizationPermissionDenied):
            staff.invite(
                _owner_context(),
                email="other@example.test",
                role=UserRole.COMPANY_OWNER,
            )
    finally:
        engine.dispose()
