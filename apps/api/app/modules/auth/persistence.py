"""Synchronous SQLAlchemy persistence for provisioned users and server sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.modules.auth.models import AuthUser, ServerSession
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
    sa.Column("password_hash", sa.String(length=512), nullable=False),
    sa.Column("encrypted_totp_secret", sa.Text(), nullable=True),
    sa.Column("recovery_code_hashes", sa.JSON(), nullable=False),
    sa.UniqueConstraint("organization_id", "email", name="uq_app_users_organization_email"),
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

    def get_user_by_email(self, email: str) -> AuthUser | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(app_users).where(app_users.c.email == email)
            ).mappings().first()
        return _to_user(row) if row is not None else None

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
        revoked_at=_as_utc(row["revoked_at"]) if row["revoked_at"] is not None else None,
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
