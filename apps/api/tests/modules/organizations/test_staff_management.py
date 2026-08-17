"""Organization-owner staff lifecycle contracts."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from app.modules.auth.persistence import AUTH_METADATA, SqlAlchemyAuthRepository
from app.modules.organizations.models import UserRole
from app.modules.organizations.provisioning import ProvisioningService, StaffInvitationService, StaffManagementService
from app.modules.organizations.service import OrganizationPermissionDenied
from app.modules.shared.commands import TenantContext


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
KEY = b"p" * 32


def test_company_owner_lists_and_deactivates_only_their_staff(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'staff-management.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    owners = ProvisioningService(repository, encryption_key=KEY, now=lambda: NOW)
    invitations = StaffInvitationService(repository, now=lambda: NOW)
    management = StaffManagementService(repository, now=lambda: NOW)
    try:
        owner = owners.provision("Acme", "owner@example.test")
        owner_user = repository.get_user_by_email(owner.owner_email)
        assert owner_user is not None
        context = TenantContext(owner.organization_id, owner_user.id, frozenset({"company_owner"}))
        pending = invitations.invite(context, email="manager@example.test", role=UserRole.MANAGER)

        members = management.list_members(context)
        deactivated = management.deactivate_member(context, pending.user_id)

        assert [member.email for member in members] == ["manager@example.test"]
        assert deactivated.disabled_at == NOW
        stored = repository.get_user_by_id(pending.user_id)
        assert stored is not None and stored.disabled_at == NOW
    finally:
        engine.dispose()


def test_owner_cannot_deactivate_self_or_another_owner(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'staff-management-denied.db'}")
    AUTH_METADATA.create_all(engine)
    repository = SqlAlchemyAuthRepository(engine)
    owners = ProvisioningService(repository, encryption_key=KEY, now=lambda: NOW)
    management = StaffManagementService(repository, now=lambda: NOW)
    try:
        owner = owners.provision("Acme", "owner@example.test")
        owner_user = repository.get_user_by_email(owner.owner_email)
        assert owner_user is not None
        context = TenantContext(owner.organization_id, owner_user.id, frozenset({"company_owner"}))

        with pytest.raises(OrganizationPermissionDenied):
            management.deactivate_member(context, owner_user.id)
    finally:
        engine.dispose()
