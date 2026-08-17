"""Synchronous SQLAlchemy persistence for provisioned users and server sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.modules.auth.models import AuthUser, ServerSession, SetupInvitation, TotpEnrollmentChallenge
from app.modules.auth.passwords import verify_recovery_code
from app.modules.organizations.models import UserRole


AUTH_METADATA = sa.MetaData()

organizations = sa.Table(
    "organizations",
    AUTH_METADATA,
    sa.Column("organization_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("name", sa.String(length=256), nullable=False, unique=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
)

app_users = sa.Table(
    "app_users",
    AUTH_METADATA,
    sa.Column("user_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("organization_id", sa.Uuid(as_uuid=True), sa.ForeignKey("organizations.organization_id"), nullable=False),
    sa.Column("email", sa.String(length=320), nullable=False),
    sa.Column("role", sa.String(length=32), nullable=False),
    sa.Column("password_hash", sa.String(length=512), nullable=True),
    sa.Column("encrypted_totp_secret", sa.Text(), nullable=True),
    sa.Column("recovery_code_hashes", sa.JSON(), nullable=False),
    sa.UniqueConstraint("email", name="uq_app_users_email"),
)

auth_setup_invitations = sa.Table(
    "auth_setup_invitations",
    AUTH_METADATA,
    sa.Column("invitation_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("app_users.user_id"), nullable=False),
    sa.Column("token_hash", sa.String(length=512), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("user_id", name="uq_auth_setup_invitations_user_id"),
)

auth_totp_enrollments = sa.Table(
    "auth_totp_enrollments",
    AUTH_METADATA,
    sa.Column("enrollment_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("app_users.user_id"), nullable=False),
    sa.Column("token_hash", sa.String(length=512), nullable=False),
    sa.Column("encrypted_secret", sa.Text(), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("user_id", name="uq_auth_totp_enrollments_user_id"),
)

auth_sessions = sa.Table(
    "auth_sessions",
    AUTH_METADATA,
    sa.Column("session_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("app_users.user_id"), nullable=False),
    sa.Column("organization_id", sa.Uuid(as_uuid=True), sa.ForeignKey("organizations.organization_id"), nullable=False),
    sa.Column("roles", sa.JSON(), nullable=False),
    sa.Column("mfa_verified", sa.Boolean(), nullable=False),
    sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
)


class SqlAlchemyAuthRepository:
    """SQL persistence boundary; application services remain storage-agnostic."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_organization(self, organization_id: UUID, name: str) -> None:
        with self._engine.begin() as connection:
            existing = connection.execute(
                sa.select(organizations.c.organization_id).where(
                    organizations.c.organization_id == organization_id
                )
            ).first()
            if existing is None:
                connection.execute(
                    sa.insert(organizations).values(organization_id=organization_id, name=name)
                )

    def provision_company_owner(
        self,
        *,
        organization_id: UUID,
        organization_name: str,
        user: AuthUser,
        invitation: SetupInvitation,
    ) -> None:
        """Persist the initial tenant boundary, owner, and setup grant atomically."""
        with self._engine.begin() as connection:
            connection.execute(
                sa.insert(organizations).values(organization_id=organization_id, name=organization_name)
            )
            connection.execute(
                sa.insert(app_users).values(
                    user_id=user.id,
                    organization_id=user.organization_id,
                    email=user.email,
                    role=user.role.value,
                    password_hash=user.password_hash,
                    encrypted_totp_secret=user.encrypted_totp_secret,
                    recovery_code_hashes=list(user.recovery_code_hashes),
                )
            )
            connection.execute(
                sa.insert(auth_setup_invitations).values(
                    invitation_id=invitation.id,
                    user_id=invitation.user_id,
                    token_hash=invitation.token_hash,
                    expires_at=invitation.expires_at,
                    consumed_at=invitation.consumed_at,
                )
            )

    def get_user_by_email(self, email: str) -> AuthUser | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(app_users).where(app_users.c.email == email)
            ).mappings().first()
        return _to_user(row) if row is not None else None

    def get_user_by_id(self, user_id: UUID) -> AuthUser | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(app_users).where(app_users.c.user_id == user_id)
            ).mappings().first()
        return _to_user(row) if row is not None else None

    def get_setup_invitation(self, user_id: UUID | None) -> SetupInvitation | None:
        if user_id is None:
            return None
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(auth_setup_invitations).where(auth_setup_invitations.c.user_id == user_id)
            ).mappings().first()
        return _to_setup_invitation(row) if row is not None else None

    def get_setup_invitation_by_id(self, invitation_id: UUID) -> SetupInvitation | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(auth_setup_invitations).where(
                    auth_setup_invitations.c.invitation_id == invitation_id
                )
            ).mappings().first()
        return _to_setup_invitation(row) if row is not None else None

    def create_totp_enrollment(self, challenge: TotpEnrollmentChallenge) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                sa.insert(auth_totp_enrollments).values(
                    enrollment_id=challenge.id,
                    user_id=challenge.user_id,
                    token_hash=challenge.token_hash,
                    encrypted_secret=challenge.encrypted_secret,
                    expires_at=challenge.expires_at,
                    consumed_at=challenge.consumed_at,
                )
            )

    def get_totp_enrollment(self, enrollment_id: UUID) -> TotpEnrollmentChallenge | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(auth_totp_enrollments).where(
                    auth_totp_enrollments.c.enrollment_id == enrollment_id
                )
            ).mappings().first()
        return _to_totp_enrollment(row) if row is not None else None

    def consume_totp_enrollment(
        self,
        *,
        challenge: TotpEnrollmentChallenge,
        enrollment_token: str,
        recovery_code_hashes: tuple[str, ...],
        now: datetime,
    ) -> bool:
        """Atomically consume an enrollment after its TOTP code was verified."""
        with self._engine.begin() as connection:
            row = connection.execute(
                sa.select(auth_totp_enrollments).where(
                    auth_totp_enrollments.c.enrollment_id == challenge.id
                )
            ).mappings().first()
            if (
                row is None
                or row["user_id"] != challenge.user_id
                or row["consumed_at"] is not None
                or _as_utc(row["expires_at"]) <= now
                or not verify_recovery_code(enrollment_token, row["token_hash"])
            ):
                return False
            consumed = connection.execute(
                sa.update(auth_totp_enrollments)
                .where(
                    auth_totp_enrollments.c.enrollment_id == challenge.id,
                    auth_totp_enrollments.c.user_id == challenge.user_id,
                    auth_totp_enrollments.c.consumed_at.is_(None),
                    auth_totp_enrollments.c.expires_at > now,
                    auth_totp_enrollments.c.token_hash == challenge.token_hash,
                    auth_totp_enrollments.c.encrypted_secret == challenge.encrypted_secret,
                )
                .values(consumed_at=now)
            )
            if consumed.rowcount != 1:
                return False
            connection.execute(
                sa.update(app_users)
                .where(app_users.c.user_id == challenge.user_id)
                .values(
                    encrypted_totp_secret=challenge.encrypted_secret,
                    recovery_code_hashes=list(recovery_code_hashes),
                )
            )
            return True

    def consume_setup_invitation(
        self,
        *,
        invitation_id: UUID,
        setup_token: str,
        password_hash: str,
        now: datetime,
    ) -> UUID | None:
        """Consume a valid setup grant and set its user's password exactly once."""
        with self._engine.begin() as connection:
            row = connection.execute(
                sa.select(auth_setup_invitations).where(
                    auth_setup_invitations.c.invitation_id == invitation_id
                )
            ).mappings().first()
            if (
                row is None
                or row["consumed_at"] is not None
                or _as_utc(row["expires_at"]) <= now
                or not verify_recovery_code(setup_token, row["token_hash"])
            ):
                return None
            consumed = connection.execute(
                sa.update(auth_setup_invitations)
                .where(
                    auth_setup_invitations.c.invitation_id == invitation_id,
                    auth_setup_invitations.c.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            if consumed.rowcount != 1:
                return None
            connection.execute(
                sa.update(app_users)
                .where(app_users.c.user_id == row["user_id"])
                .values(password_hash=password_hash)
            )
            return row["user_id"]

    def consume_setup_invitation_and_create_totp_enrollment(
        self,
        *,
        invitation_id: UUID,
        setup_token: str,
        password_hash: str,
        challenge: TotpEnrollmentChallenge,
        now: datetime,
    ) -> UUID | None:
        """Atomically consume setup material, set a password, and create TOTP enrollment."""
        with self._engine.begin() as connection:
            row = connection.execute(
                sa.select(auth_setup_invitations).where(
                    auth_setup_invitations.c.invitation_id == invitation_id
                )
            ).mappings().first()
            if (
                row is None
                or row["consumed_at"] is not None
                or _as_utc(row["expires_at"]) <= now
                or not verify_recovery_code(setup_token, row["token_hash"])
                or row["user_id"] != challenge.user_id
            ):
                return None
            consumed = connection.execute(
                sa.update(auth_setup_invitations)
                .where(
                    auth_setup_invitations.c.invitation_id == invitation_id,
                    auth_setup_invitations.c.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            if consumed.rowcount != 1:
                return None
            connection.execute(
                sa.update(app_users)
                .where(app_users.c.user_id == row["user_id"])
                .values(password_hash=password_hash)
            )
            connection.execute(
                sa.insert(auth_totp_enrollments).values(
                    enrollment_id=challenge.id,
                    user_id=challenge.user_id,
                    token_hash=challenge.token_hash,
                    encrypted_secret=challenge.encrypted_secret,
                    expires_at=challenge.expires_at,
                    consumed_at=challenge.consumed_at,
                )
            )
            return row["user_id"]

    def save_user(self, user: AuthUser) -> None:
        values = {
            "organization_id": user.organization_id,
            "email": user.email,
            "role": user.role.value,
            "password_hash": user.password_hash,
            "encrypted_totp_secret": user.encrypted_totp_secret,
            "recovery_code_hashes": list(user.recovery_code_hashes),
        }
        with self._engine.begin() as connection:
            result = connection.execute(
                sa.update(app_users).where(app_users.c.user_id == user.id).values(**values)
            )
            if result.rowcount == 0:
                connection.execute(sa.insert(app_users).values(user_id=user.id, **values))

    def save_session(self, session: ServerSession) -> None:
        values = {
            "user_id": session.user_id,
            "organization_id": session.organization_id,
            "roles": sorted(session.roles),
            "mfa_verified": session.mfa_verified,
            "issued_at": session.issued_at,
            "last_active_at": session.last_active_at or session.issued_at,
            "expires_at": session.expires_at or session.issued_at,
            "revoked_at": session.revoked_at,
        }
        with self._engine.begin() as connection:
            result = connection.execute(
                sa.update(auth_sessions)
                .where(auth_sessions.c.session_id == session.id)
                .values(**values)
            )
            if result.rowcount == 0:
                connection.execute(sa.insert(auth_sessions).values(session_id=session.id, **values))

    def get_session(self, session_id: UUID) -> ServerSession | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(auth_sessions).where(auth_sessions.c.session_id == session_id)
            ).mappings().first()
        return _to_session(row) if row is not None else None


def _to_user(row) -> AuthUser:
    return AuthUser(
        id=row["user_id"],
        organization_id=row["organization_id"],
        email=row["email"],
        role=UserRole(row["role"]),
        password_hash=row["password_hash"],
        encrypted_totp_secret=row["encrypted_totp_secret"],
        recovery_code_hashes=tuple(row["recovery_code_hashes"]),
    )


def _to_session(row) -> ServerSession:
    return ServerSession(
        id=row["session_id"],
        user_id=row["user_id"],
        organization_id=row["organization_id"],
        roles=frozenset(row["roles"]),
        mfa_verified=row["mfa_verified"],
        issued_at=_as_utc(row["issued_at"]),
        last_active_at=_as_utc(row["last_active_at"]),
        expires_at=_as_utc(row["expires_at"]),
        revoked_at=_as_utc(row["revoked_at"]) if row["revoked_at"] is not None else None,
    )


def _to_setup_invitation(row) -> SetupInvitation:
    return SetupInvitation(
        id=row["invitation_id"],
        user_id=row["user_id"],
        token_hash=row["token_hash"],
        expires_at=_as_utc(row["expires_at"]),
        consumed_at=_as_utc(row["consumed_at"]) if row["consumed_at"] is not None else None,
    )


def _to_totp_enrollment(row) -> TotpEnrollmentChallenge:
    return TotpEnrollmentChallenge(
        id=row["enrollment_id"],
        user_id=row["user_id"],
        token_hash=row["token_hash"],
        encrypted_secret=row["encrypted_secret"],
        expires_at=_as_utc(row["expires_at"]),
        consumed_at=_as_utc(row["consumed_at"]) if row["consumed_at"] is not None else None,
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
