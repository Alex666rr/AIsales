"""One-time initial organization provisioning primitives."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import AuthUser, SetupInvitation, TotpEnrollmentChallenge
from app.modules.auth.passwords import hash_password, hash_recovery_code
from app.modules.auth.totp import encrypt_totp_secret, enrollment_uri, generate_totp_secret
from app.modules.organizations.models import UserRole
from app.modules.organizations.service import OrganizationPermissionDenied
from app.modules.shared.commands import TenantContext


@dataclass(frozen=True, slots=True)
class ProvisionedOwner:
    organization_id: UUID
    owner_email: str
    setup_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProvisionedMember:
    """A staff user and their one-time setup grant, disclosed only to the owner."""

    user_id: UUID
    email: str
    role: UserRole
    setup_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PendingTotpEnrollment:
    """One-time scan material returned immediately after successful first-password setup."""

    enrollment_id: UUID
    enrollment_token: str = field(repr=False)
    totp_uri: str = field(repr=False)


class ProvisioningRepository(Protocol):
    def provision_company_owner(
        self,
        *,
        organization_id: UUID,
        organization_name: str,
        user: AuthUser,
        invitation: SetupInvitation,
    ) -> None: ...

    def get_user_by_id(self, user_id: UUID) -> AuthUser | None: ...

    def get_setup_invitation_by_id(self, invitation_id: UUID) -> SetupInvitation | None: ...

    def consume_setup_invitation(
        self,
        *,
        invitation_id: UUID,
        setup_token: str,
        password_hash: str,
        now: datetime,
    ) -> UUID | None: ...


class StaffInvitationRepository(Protocol):
    """Atomically write a pending staff member and its setup grant."""

    def create_member_with_setup_invitation(
        self,
        *,
        user: AuthUser,
        invitation: SetupInvitation,
    ) -> None: ...

    def consume_setup_invitation_and_create_totp_enrollment(
        self,
        *,
        invitation_id: UUID,
        setup_token: str,
        password_hash: str,
        challenge: TotpEnrollmentChallenge,
        now: datetime,
    ) -> UUID | None: ...


class SetupTokenDenied(PermissionError):
    """The setup token is malformed, expired, already used, or not recognized."""


class ProvisioningService:
    """Create the first company owner with a non-reversible setup grant."""

    _SETUP_TOKEN_TTL = timedelta(hours=48)

    def __init__(
        self,
        repository: ProvisioningRepository,
        *,
        encryption_key: bytes,
        now: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._encryption_key = encryption_key
        self._now = now

    def provision(self, organization_name: str, owner_email: str) -> ProvisionedOwner:
        if not organization_name.strip() or "@" not in owner_email:
            raise ValueError("invalid provisioning request")
        organization_id = uuid4()
        user = AuthUser(
            id=uuid4(),
            organization_id=organization_id,
            email=owner_email,
            role=UserRole.COMPANY_OWNER,
            password_hash=None,
            encrypted_totp_secret=None,
            recovery_code_hashes=(),
        )
        invitation_id = uuid4()
        setup_token = f"{invitation_id}.{secrets.token_urlsafe(32)}"
        invitation = SetupInvitation(
            id=invitation_id,
            user_id=user.id,
            token_hash=hash_recovery_code(setup_token),
            expires_at=self._now() + self._SETUP_TOKEN_TTL,
        )
        try:
            self._repository.provision_company_owner(
                organization_id=organization_id,
                organization_name=organization_name.strip(),
                user=user,
                invitation=invitation,
            )
        except IntegrityError as exc:
            raise ValueError("owner email already exists") from exc
        return ProvisionedOwner(
            organization_id=organization_id,
            owner_email=owner_email,
            setup_token=setup_token,
        )

    def activate_setup_token(self, setup_token: str, *, password: str) -> PendingTotpEnrollment:
        """Set the first password only when a non-expired setup token wins consumption."""
        invitation_id = _invitation_id_from_token(setup_token)
        if invitation_id is None or not password:
            raise SetupTokenDenied("setup token was not accepted")
        invitation = self._repository.get_setup_invitation_by_id(invitation_id)
        if invitation is None:
            raise SetupTokenDenied("setup token was not accepted")
        user = self._repository.get_user_by_id(invitation.user_id)
        if user is None:
            raise SetupTokenDenied("setup token was not accepted")
        now = self._now()
        enrollment_id = uuid4()
        enrollment_token = f"{enrollment_id}.{secrets.token_urlsafe(32)}"
        totp_secret = generate_totp_secret()
        challenge = TotpEnrollmentChallenge(
            id=enrollment_id,
            user_id=user.id,
            token_hash=hash_recovery_code(enrollment_token),
            encrypted_secret=encrypt_totp_secret(totp_secret, self._encryption_key),
            expires_at=now + timedelta(minutes=10),
        )
        user_id = self._repository.consume_setup_invitation_and_create_totp_enrollment(
            invitation_id=invitation_id,
            setup_token=setup_token,
            password_hash=hash_password(password),
            challenge=challenge,
            now=now,
        )
        if user_id is None:
            raise SetupTokenDenied("setup token was not accepted")
        return PendingTotpEnrollment(
            enrollment_id=enrollment_id,
            enrollment_token=enrollment_token,
            totp_uri=enrollment_uri(secret=totp_secret, email=user.email),
        )


class StaffInvitationService:
    """Allow a company owner to issue a one-time setup grant for internal staff."""

    _SETUP_TOKEN_TTL = timedelta(hours=48)
    _INVITABLE_ROLES = frozenset({UserRole.ADMINISTRATOR, UserRole.MANAGER})

    def __init__(self, repository: StaffInvitationRepository, *, now: Callable[[], datetime]) -> None:
        self._repository = repository
        self._now = now

    def invite(
        self,
        context: TenantContext,
        *,
        email: str,
        role: UserRole,
    ) -> ProvisionedMember:
        if UserRole.COMPANY_OWNER.value not in context.roles or role not in self._INVITABLE_ROLES:
            raise OrganizationPermissionDenied("company owner role is required")
        if not email.strip() or "@" not in email:
            raise ValueError("invalid staff invitation")
        user = AuthUser(
            id=uuid4(),
            organization_id=context.organization_id,
            email=email.strip(),
            role=role,
            password_hash=None,
            encrypted_totp_secret=None,
            recovery_code_hashes=(),
        )
        invitation_id = uuid4()
        setup_token = f"{invitation_id}.{secrets.token_urlsafe(32)}"
        invitation = SetupInvitation(
            id=invitation_id,
            user_id=user.id,
            token_hash=hash_recovery_code(setup_token),
            expires_at=self._now() + self._SETUP_TOKEN_TTL,
        )
        try:
            self._repository.create_member_with_setup_invitation(user=user, invitation=invitation)
        except IntegrityError as exc:
            raise ValueError("staff email already exists") from exc
        return ProvisionedMember(
            user_id=user.id,
            email=user.email,
            role=user.role,
            setup_token=setup_token,
        )


def _invitation_id_from_token(setup_token: str) -> UUID | None:
    candidate, separator, secret = setup_token.partition(".")
    if not separator or not secret:
        return None
    try:
        return UUID(candidate)
    except ValueError:
        return None
