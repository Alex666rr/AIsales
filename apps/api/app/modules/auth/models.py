"""Secret-safe authentication value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.modules.organizations.models import UserRole


@dataclass(frozen=True, slots=True)
class AuthUser:
    """A provisioned user with only one-way or encrypted credentials."""

    id: UUID
    organization_id: UUID
    email: str
    role: UserRole
    password_hash: str | None
    encrypted_totp_secret: str | None
    recovery_code_hashes: tuple[str, ...]
    disabled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SetupInvitation:
    """A one-time password setup grant whose raw secret is never persisted."""

    id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TotpEnrollmentChallenge:
    """Short-lived enrollment material; both token and secret remain non-plaintext at rest."""

    id: UUID
    user_id: UUID
    token_hash: str
    encrypted_secret: str
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecoveryCodes:
    """Plaintext emergency codes, returned once and never persisted."""

    values: tuple[str, ...] = field(repr=False)

    def __iter__(self):
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


@dataclass(frozen=True, slots=True)
class ServerSession:
    """Server-side authentication session; never a bearer secret itself."""

    id: UUID
    user_id: UUID
    organization_id: UUID
    roles: frozenset[str]
    mfa_verified: bool
    issued_at: datetime
    revoked_at: datetime | None = None
    last_active_at: datetime | None = None
    expires_at: datetime | None = None
