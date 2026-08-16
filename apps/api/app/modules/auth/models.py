"""Secret-safe authentication value objects."""

from __future__ import annotations

from dataclasses import dataclass
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
    password_hash: str
    encrypted_totp_secret: str | None
    recovery_code_hashes: tuple[str, ...]


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
