"""One-time initial organization provisioning primitives."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import AuthUser, SetupInvitation
from app.modules.auth.passwords import hash_password, hash_recovery_code
from app.modules.organizations.models import UserRole


@dataclass(frozen=True, slots=True)
class ProvisionedOwner:
    organization_id: UUID
    owner_email: str
    setup_token: str = field(repr=False)


class ProvisioningRepository(Protocol):
    def provision_company_owner(
        self,
        *,
        organization_id: UUID,
        organization_name: str,
        user: AuthUser,
        invitation: SetupInvitation,
    ) -> None: ...

    def consume_setup_invitation(
        self,
        *,
        invitation_id: UUID,
        setup_token: str,
        password_hash: str,
        now: datetime,
    ) -> UUID | None: ...


class SetupTokenDenied(PermissionError):
    """The setup token is malformed, expired, already used, or not recognized."""


class ProvisioningService:
    """Create the first company owner with a non-reversible setup grant."""

    _SETUP_TOKEN_TTL = timedelta(hours=48)

    def __init__(self, repository: ProvisioningRepository, *, now: Callable[[], datetime]) -> None:
        self._repository = repository
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

    def activate_setup_token(self, setup_token: str, *, password: str) -> UUID:
        """Set the first password only when a non-expired setup token wins consumption."""
        invitation_id = _invitation_id_from_token(setup_token)
        if invitation_id is None or not password:
            raise SetupTokenDenied("setup token was not accepted")
        user_id = self._repository.consume_setup_invitation(
            invitation_id=invitation_id,
            setup_token=setup_token,
            password_hash=hash_password(password),
            now=self._now(),
        )
        if user_id is None:
            raise SetupTokenDenied("setup token was not accepted")
        return user_id


def _invitation_id_from_token(setup_token: str) -> UUID | None:
    candidate, separator, secret = setup_token.partition(".")
    if not separator or not secret:
        return None
    try:
        return UUID(candidate)
    except ValueError:
        return None
