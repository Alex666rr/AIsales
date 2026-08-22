"""SQLAlchemy repositories for authoritative Stage 0 Telegram state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from telegram_connector.proxies import (
    ProxyConfig,
    ProxyConfigurationError,
    ProxyReservation,
)
from telegram_connector.runtime.connection import ConnectionHealth, ConnectionRecord
from telegram_connector.session_store import SessionRef, StoredSessionCiphertext


telegram_state_metadata = sa.MetaData()

telegram_accounts = sa.Table(
    "telegram_accounts",
    telegram_state_metadata,
    sa.Column("account_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False, index=True),
    sa.Column("telegram_user_id", sa.BigInteger(), unique=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
)

telegram_session_ciphertexts = sa.Table(
    "telegram_session_ciphertexts",
    telegram_state_metadata,
    sa.Column("session_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
    sa.Column("key_version", sa.Integer(), nullable=False),
    sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
    sa.ForeignKeyConstraint(
        ["account_id"],
        ["telegram_accounts.account_id"],
        name="fk_telegram_session_ciphertexts_account",
        ondelete="RESTRICT",
    ),
    sa.UniqueConstraint(
        "account_id",
        "session_id",
        "key_version",
        name="uq_telegram_session_ciphertexts_account_session_key",
    ),
    sa.CheckConstraint("key_version > 0", name="ck_telegram_session_ciphertexts_key_version"),
)

telegram_connections = sa.Table(
    "telegram_connections",
    telegram_state_metadata,
    sa.Column("account_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("session_id", sa.Uuid(as_uuid=True), nullable=False),
    sa.Column("key_version", sa.Integer(), nullable=False),
    sa.Column("state", sa.String(32), nullable=False),
    sa.Column("last_seen_at", sa.DateTime(timezone=True)),
    sa.Column("proxy_ip", sa.String(64)),
    sa.Column("latency_ms", sa.Integer()),
    sa.Column("error_code", sa.String(64)),
    sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("retry_at", sa.DateTime(timezone=True)),
    sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("lease_owner_id", sa.Uuid(as_uuid=True)),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    sa.Column("fence_token", sa.BigInteger(), nullable=False, server_default="0"),
    sa.ForeignKeyConstraint(
        ["account_id", "session_id", "key_version"],
        [
            "telegram_session_ciphertexts.account_id",
            "telegram_session_ciphertexts.session_id",
            "telegram_session_ciphertexts.key_version",
        ],
        name="fk_telegram_connections_account_session_key",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "state IN ('quarantine', 'active', 'paused', 'reauth_required', 'limited', 'blocked', 'archived')",
        name="ck_telegram_connections_state",
    ),
    sa.CheckConstraint("retry_count >= 0", name="ck_telegram_connections_retry_count"),
    sa.CheckConstraint("version >= 0", name="ck_telegram_connections_version"),
    sa.CheckConstraint("fence_token >= 0", name="ck_telegram_connections_fence_token"),
)

telegram_proxies = sa.Table(
    "telegram_proxies",
    telegram_state_metadata,
    sa.Column("proxy_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=True, index=True),
    sa.Column("endpoint", sa.String(512), nullable=False),
    sa.Column("capacity", sa.Integer(), nullable=False),
    sa.Column("credential_key_version", sa.Integer()),
    sa.Column("credential_ciphertext", sa.LargeBinary()),
    sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.CheckConstraint("capacity BETWEEN 1 AND 5", name="ck_telegram_proxies_capacity"),
    sa.CheckConstraint(
        "(credential_key_version IS NULL) = (credential_ciphertext IS NULL)",
        name="ck_telegram_proxies_credentials_pair",
    ),
)

telegram_proxy_overrides = sa.Table(
    "telegram_proxy_overrides",
    telegram_state_metadata,
    sa.Column("account_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("proxy_id", sa.Uuid(as_uuid=True)),
    sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
    sa.ForeignKeyConstraint(
        ["account_id"], ["telegram_accounts.account_id"],
        name="fk_telegram_proxy_overrides_account", ondelete="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["proxy_id"], ["telegram_proxies.proxy_id"],
        name="fk_telegram_proxy_overrides_proxy", ondelete="RESTRICT",
    ),
    sa.CheckConstraint("revision >= 0", name="ck_telegram_proxy_overrides_revision"),
)

telegram_proxy_assignments = sa.Table(
    "telegram_proxy_assignments",
    telegram_state_metadata,
    sa.Column("account_id", sa.Uuid(as_uuid=True), primary_key=True),
    sa.Column("proxy_id", sa.Uuid(as_uuid=True), nullable=False, index=True),
    sa.Column("assignment_id", sa.Uuid(as_uuid=True), nullable=False, unique=True),
    sa.Column("assignment_revision", sa.BigInteger(), nullable=False),
    sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
    sa.ForeignKeyConstraint(
        ["account_id"], ["telegram_accounts.account_id"],
        name="fk_telegram_proxy_assignments_account", ondelete="RESTRICT",
    ),
    sa.ForeignKeyConstraint(
        ["proxy_id"], ["telegram_proxies.proxy_id"],
        name="fk_telegram_proxy_assignments_proxy", ondelete="RESTRICT",
    ),
    sa.CheckConstraint("assignment_revision >= 0", name="ck_telegram_proxy_assignments_revision"),
)


def create_telegram_state_schema(bind: Engine) -> None:
    """Create local contract-test tables; production uses the Alembic migration."""
    telegram_state_metadata.create_all(bind)


class TelegramStateRepositoryUnavailable(RuntimeError):
    """Safe persistence failure without driver, SQL, or credential detail."""

    def __init__(self) -> None:
        super().__init__("telegram state repository unavailable")


class SqlAlchemyTelegramAccountRepository:
    """Authoritative account-to-organization ownership lookup."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put(self, account_id: UUID, organization_id: UUID) -> None:
        try:
            with self._sessions.begin() as session:
                current = session.execute(
                    sa.select(telegram_accounts.c.account_id).where(
                        telegram_accounts.c.account_id == account_id
                    )
                ).scalar_one_or_none()
                if current is None:
                    session.execute(sa.insert(telegram_accounts).values(
                        account_id=account_id, organization_id=organization_id
                    ))
                else:
                    session.execute(
                        sa.update(telegram_accounts)
                        .where(telegram_accounts.c.account_id == account_id)
                        .values(organization_id=organization_id)
                    )
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def provision(self, organization_id: UUID, telegram_user_id: int) -> UUID:
        """Return the sole platform account for one Telegram numeric identity."""
        if telegram_user_id <= 0:
            raise TelegramStateRepositoryUnavailable()
        for _ in range(2):
            try:
                with self._sessions.begin() as session:
                    row = session.execute(
                        sa.select(telegram_accounts.c.account_id, telegram_accounts.c.organization_id)
                        .where(telegram_accounts.c.telegram_user_id == telegram_user_id)
                        .with_for_update()
                    ).one_or_none()
                    if row is not None:
                        if row.organization_id != organization_id:
                            raise TelegramStateRepositoryUnavailable()
                        return row.account_id
                    account_id = uuid4()
                    session.execute(sa.insert(telegram_accounts).values(
                        account_id=account_id,
                        organization_id=organization_id,
                        telegram_user_id=telegram_user_id,
                    ))
                    return account_id
            except TelegramStateRepositoryUnavailable:
                raise
            except sa.exc.IntegrityError:
                continue
            except Exception:
                raise TelegramStateRepositoryUnavailable() from None
        raise TelegramStateRepositoryUnavailable()

    async def provision_async(self, organization_id: UUID, telegram_user_id: int) -> UUID:
        return await asyncio.to_thread(self.provision, organization_id, telegram_user_id)

    def organization_for(self, account_id: UUID) -> UUID | None:
        try:
            with self._sessions() as session:
                return session.execute(
                    sa.select(telegram_accounts.c.organization_id).where(
                        telegram_accounts.c.account_id == account_id
                    )
                ).scalar_one_or_none()
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    async def organization_for_async(self, account_id: UUID) -> UUID | None:
        return await asyncio.to_thread(self.organization_for, account_id)

    def list_account_ids_by_organization(self, organization_id: UUID) -> tuple[UUID, ...]:
        try:
            with self._sessions() as session:
                return tuple(
                    session.execute(
                        sa.select(telegram_accounts.c.account_id)
                        .where(telegram_accounts.c.organization_id == organization_id)
                        .order_by(telegram_accounts.c.account_id)
                    ).scalars()
                )
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    async def list_account_ids_by_organization_async(
        self, organization_id: UUID
    ) -> tuple[UUID, ...]:
        return await asyncio.to_thread(self.list_account_ids_by_organization, organization_id)


class SqlAlchemyCiphertextSessionRepository:
    """Synchronous ciphertext repository for the psycopg-backed encryption boundary."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def save(self, record: StoredSessionCiphertext) -> None:
        try:
            with self._sessions.begin() as session:
                session.execute(sa.insert(telegram_session_ciphertexts).values(
                    session_id=record.session_id,
                    account_id=record.account_id,
                    key_version=record.key_version,
                    ciphertext=record.ciphertext,
                ))
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def find(self, session_id: UUID) -> StoredSessionCiphertext | None:
        try:
            with self._sessions() as session:
                row = session.execute(
                    sa.select(telegram_session_ciphertexts).where(
                        telegram_session_ciphertexts.c.session_id == session_id
                    )
                ).mappings().one_or_none()
            return None if row is None else _stored_session(row)
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def records(self) -> tuple[StoredSessionCiphertext, ...]:
        try:
            with self._sessions() as session:
                rows = session.execute(
                    sa.select(telegram_session_ciphertexts).order_by(
                        telegram_session_ciphertexts.c.session_id
                    )
                ).mappings().all()
            return tuple(_stored_session(row) for row in rows)
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None


class SqlAlchemyConnectionRepository:
    """PostgreSQL row-lock/CAS repository with database-time lease decisions."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    async def get(self, account_id: UUID) -> ConnectionRecord | None:
        return await asyncio.to_thread(self._get, account_id)

    async def save(self, record: ConnectionRecord) -> None:
        await asyncio.to_thread(self._save, record)

    async def try_claim(
        self, account_id: UUID, owner_id: UUID, *, lease_seconds: float
    ) -> ConnectionRecord | None:
        return await asyncio.to_thread(self._try_claim, account_id, owner_id, _duration(lease_seconds))

    async def save_claimed(
        self, record: ConnectionRecord, owner_id: UUID, *, release_lease: bool
    ) -> ConnectionRecord | None:
        return await asyncio.to_thread(self._save_claimed, record, owner_id, release_lease)

    async def force_terminal(self, account_id: UUID, state: str, now: datetime) -> ConnectionRecord:
        if state not in {"paused", "archived"}:
            raise ValueError("invalid terminal state")
        return await asyncio.to_thread(self._force_terminal, account_id, state)

    async def renew_lease(
        self, record: ConnectionRecord, owner_id: UUID, *, lease_seconds: float
    ) -> ConnectionRecord | None:
        return await asyncio.to_thread(self._renew_lease, record, owner_id, _duration(lease_seconds))

    async def fail_closed(self, record: ConnectionRecord, owner_id: UUID) -> ConnectionRecord | None:
        return await asyncio.to_thread(self._fail_closed, record, owner_id)

    def _get(self, account_id: UUID) -> ConnectionRecord | None:
        try:
            with self._sessions() as session:
                row = session.execute(
                    sa.select(telegram_connections).where(
                        telegram_connections.c.account_id == account_id
                    )
                ).mappings().one_or_none()
            return None if row is None else _connection_record(row)
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _save(self, record: ConnectionRecord) -> None:
        _validate_connection_record(record)
        try:
            with self._sessions.begin() as session:
                self._require_session(session, record.session_ref)
                current = self._locked_row(session, record.account_id)
                values = _connection_values(record)
                if current is None:
                    session.execute(sa.insert(telegram_connections).values(**values))
                else:
                    if current["lease_owner_id"] is not None or record.lease_owner_id is not None:
                        raise TelegramStateRepositoryUnavailable()
                    session.execute(
                        sa.update(telegram_connections)
                        .where(telegram_connections.c.account_id == record.account_id)
                        .values(**values)
                    )
        except TelegramStateRepositoryUnavailable:
            raise
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _try_claim(
        self, account_id: UUID, owner_id: UUID, lease_duration: timedelta
    ) -> ConnectionRecord | None:
        try:
            with self._sessions.begin() as session:
                row = self._locked_row(session, account_id)
                if row is None:
                    return None
                record = _connection_record(row)
                if record.health.state in {"paused", "reauth_required", "blocked", "archived"}:
                    return record
                now = _database_now(session)
                if (
                    record.lease_owner_id is not None
                    and (record.lease_expires_at is None or record.lease_expires_at > now)
                ):
                    return None
                values = {
                    "version": record.version + 1,
                    "lease_owner_id": owner_id,
                    "lease_expires_at": now + lease_duration,
                    "fence_token": record.fence_token + 1,
                }
                session.execute(
                    sa.update(telegram_connections)
                    .where(
                        telegram_connections.c.account_id == account_id,
                        telegram_connections.c.version == record.version,
                        telegram_connections.c.fence_token == record.fence_token,
                    )
                    .values(**values)
                )
                return record.model_copy(update=values)
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _save_claimed(
        self, record: ConnectionRecord, owner_id: UUID, release_lease: bool
    ) -> ConnectionRecord | None:
        _validate_connection_record(record)
        try:
            with self._sessions.begin() as session:
                self._require_session(session, record.session_ref)
                row = self._locked_row(session, record.account_id)
                if row is None:
                    return None
                current = _connection_record(row)
                now = _database_now(session)
                if not _claim_matches(current, record, owner_id, now, require_live=True):
                    return None
                saved = record.model_copy(update={
                    "version": record.version + 1,
                    "lease_owner_id": None if release_lease else owner_id,
                    "lease_expires_at": None if release_lease else record.lease_expires_at,
                })
                changed = session.execute(
                    sa.update(telegram_connections)
                    .where(
                        telegram_connections.c.account_id == record.account_id,
                        telegram_connections.c.version == record.version,
                        telegram_connections.c.fence_token == record.fence_token,
                        telegram_connections.c.lease_owner_id == owner_id,
                    )
                    .values(**_connection_values(saved))
                ).rowcount
                return saved if changed == 1 else None
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _force_terminal(self, account_id: UUID, state: str) -> ConnectionRecord:
        try:
            with self._sessions.begin() as session:
                row = self._locked_row(session, account_id)
                if row is None:
                    raise KeyError("connection record was not found")
                record = _connection_record(row)
                if record.health.state == "archived":
                    return record
                now = _database_now(session)
                health = record.health.model_copy(
                    update={"state": state, "last_seen_at": now, "error_code": None}
                )
                saved = record.model_copy(update={
                    "health": health,
                    "retry_at": None,
                    "version": record.version + 1,
                    "lease_owner_id": None,
                    "lease_expires_at": None,
                    "fence_token": record.fence_token + 1,
                })
                session.execute(
                    sa.update(telegram_connections)
                    .where(
                        telegram_connections.c.account_id == account_id,
                        telegram_connections.c.version == record.version,
                    )
                    .values(**_connection_values(saved))
                )
                return saved
        except KeyError:
            raise
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _renew_lease(
        self, record: ConnectionRecord, owner_id: UUID, lease_duration: timedelta
    ) -> ConnectionRecord | None:
        try:
            with self._sessions.begin() as session:
                row = self._locked_row(session, record.account_id)
                if row is None:
                    return None
                current = _connection_record(row)
                now = _database_now(session)
                if (
                    not _claim_matches(current, record, owner_id, now, require_live=True)
                    or current.health.state in {"paused", "reauth_required", "blocked", "archived"}
                ):
                    return None
                saved = current.model_copy(update={
                    "version": current.version + 1,
                    "lease_expires_at": now + lease_duration,
                })
                session.execute(
                    sa.update(telegram_connections)
                    .where(
                        telegram_connections.c.account_id == record.account_id,
                        telegram_connections.c.version == record.version,
                        telegram_connections.c.fence_token == record.fence_token,
                        telegram_connections.c.lease_owner_id == owner_id,
                    )
                    .values(
                        version=saved.version,
                        lease_expires_at=saved.lease_expires_at,
                    )
                )
                return saved
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _fail_closed(self, record: ConnectionRecord, owner_id: UUID) -> ConnectionRecord | None:
        try:
            with self._sessions.begin() as session:
                row = self._locked_row(session, record.account_id)
                if row is None:
                    return None
                current = _connection_record(row)
                if not _claim_matches(current, record, owner_id, None, require_live=False):
                    return None
                now = _database_now(session)
                health = current.health.model_copy(update={
                    "state": "quarantine",
                    "last_seen_at": now,
                    "proxy_ip": None,
                    "latency_ms": None,
                    "error_code": "monitor_failed",
                })
                saved = current.model_copy(update={
                    "health": health,
                    "retry_at": None,
                    "version": current.version + 1,
                    "lease_owner_id": None,
                    "lease_expires_at": None,
                })
                session.execute(
                    sa.update(telegram_connections)
                    .where(
                        telegram_connections.c.account_id == record.account_id,
                        telegram_connections.c.version == record.version,
                        telegram_connections.c.fence_token == record.fence_token,
                        telegram_connections.c.lease_owner_id == owner_id,
                    )
                    .values(**_connection_values(saved))
                )
                return saved
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    @staticmethod
    def _locked_row(session: Session, account_id: UUID):
        return session.execute(
            sa.select(telegram_connections)
            .where(telegram_connections.c.account_id == account_id)
            .with_for_update()
        ).mappings().one_or_none()

    @staticmethod
    def _require_session(session: Session, reference: SessionRef) -> None:
        exists = session.execute(
            sa.select(telegram_session_ciphertexts.c.session_id).where(
                telegram_session_ciphertexts.c.session_id == reference.session_id,
                telegram_session_ciphertexts.c.account_id == reference.account_id,
                telegram_session_ciphertexts.c.key_version == reference.key_version,
            )
        ).scalar_one_or_none()
        if exists is None:
            raise ValueError("encrypted session reference was not found")


class ProxyCredentialCipher:
    """Authenticated encryption for optional proxy credentials at rest."""

    def __init__(
        self,
        keys: dict[int, bytes | bytearray | memoryview],
        *,
        active_key_version: int,
    ) -> None:
        if active_key_version not in keys:
            raise ValueError("active proxy credential key version is unavailable")
        normalized: dict[int, bytes] = {}
        for version, key in keys.items():
            if not isinstance(key, (bytes, bytearray, memoryview)):
                raise TypeError("proxy credential key material must be bytes-like")
            value = bytes(key)
            if len(value) not in {16, 24, 32}:
                raise ValueError("proxy credential keys must be AES compatible")
            normalized[version] = value
        self._keys = normalized
        self._active_key_version = active_key_version

    def encrypt(self, proxy: ProxyConfig) -> tuple[int | None, bytes | None]:
        if proxy.username is None:
            return None, None
        payload = json.dumps(
            {
                "username": proxy.username.get_secret_value(),
                "password": None if proxy.password is None else proxy.password.get_secret_value(),
            },
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        nonce = os.urandom(12)
        version = self._active_key_version
        ciphertext = nonce + AESGCM(self._keys[version]).encrypt(
            nonce, payload, _proxy_aad(proxy.proxy_id, proxy.endpoint, version)
        )
        return version, ciphertext

    def decrypt(
        self,
        *,
        proxy_id: UUID,
        endpoint: str,
        key_version: int | None,
        ciphertext: bytes | None,
    ) -> tuple[str | None, str | None]:
        if key_version is None and ciphertext is None:
            return None, None
        try:
            if key_version is None or ciphertext is None or len(ciphertext) < 28:
                raise ValueError
            nonce, encrypted = ciphertext[:12], ciphertext[12:]
            payload = AESGCM(self._keys[key_version]).decrypt(
                nonce, encrypted, _proxy_aad(proxy_id, endpoint, key_version)
            )
            values = json.loads(payload.decode("utf-8"))
            username = values["username"]
            password = values["password"]
            if not isinstance(username, str) or not username:
                raise ValueError
            if password is not None and not isinstance(password, str):
                raise ValueError
            return username, password
        except (InvalidTag, KeyError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            raise ProxyConfigurationError() from None


class SqlAlchemyProxyAssignmentRepository:
    """PostgreSQL proxy assignment with row-locked capacity and revision fencing."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        credential_cipher: ProxyCredentialCipher,
    ) -> None:
        self._sessions = sessions
        self._credential_cipher = credential_cipher

    async def put_proxy(
        self,
        proxy: ProxyConfig,
        *,
        default: bool = False,
        organization_id: UUID | None = None,
    ) -> None:
        await asyncio.to_thread(self._put_proxy, proxy, default, organization_id)

    async def set_account_override(self, account_id: UUID, proxy_id: UUID | None) -> None:
        await asyncio.to_thread(self._set_account_override, account_id, proxy_id)

    async def list_for_organization(self, organization_id: UUID) -> tuple[dict[str, object], ...]:
        return await asyncio.to_thread(self._list_for_organization, organization_id)

    async def reserve_assignment(self, account_id: UUID) -> ProxyReservation | None:
        return await asyncio.to_thread(self._reserve_assignment, account_id)

    async def release_failed_reservation(
        self, account_id: UUID, reservation: ProxyReservation
    ) -> None:
        await asyncio.to_thread(self._release_failed_reservation, account_id, reservation)

    async def release_terminal_assignment(self, account_id: UUID) -> None:
        await asyncio.to_thread(self._release_terminal_assignment, account_id)

    def _put_proxy(self, proxy: ProxyConfig, default: bool, organization_id: UUID | None) -> None:
        key_version, ciphertext = self._credential_cipher.encrypt(proxy)
        try:
            with self._sessions.begin() as session:
                current = self._locked_proxy_row(session, proxy.proxy_id)
                if current is not None and current["organization_id"] != organization_id:
                    raise ValueError("proxy belongs to a different organization")
                if default:
                    session.execute(
                        sa.update(telegram_proxies)
                        .where(
                            telegram_proxies.c.proxy_id != proxy.proxy_id,
                            _organization_filter(telegram_proxies.c.organization_id, organization_id),
                        )
                        .values(is_default=False)
                    )
                values = {
                    "organization_id": organization_id,
                    "endpoint": proxy.endpoint,
                    "capacity": proxy.capacity,
                    "credential_key_version": key_version,
                    "credential_ciphertext": ciphertext,
                    "is_default": default,
                }
                if current is None:
                    session.execute(sa.insert(telegram_proxies).values(proxy_id=proxy.proxy_id, **values))
                else:
                    assigned = session.execute(
                        sa.select(sa.func.count()).select_from(telegram_proxy_assignments).where(
                            telegram_proxy_assignments.c.proxy_id == proxy.proxy_id
                        )
                    ).scalar_one()
                    if assigned > proxy.capacity:
                        raise ValueError("proxy capacity is below current assignments")
                    session.execute(
                        sa.update(telegram_proxies)
                        .where(telegram_proxies.c.proxy_id == proxy.proxy_id)
                        .values(**values)
                    )
        except (ProxyConfigurationError, ValueError):
            raise
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _set_account_override(self, account_id: UUID, proxy_id: UUID | None) -> None:
        try:
            with self._sessions.begin() as session:
                if self._lock_account(session, account_id) is None:
                    raise ValueError("unknown account")
                if proxy_id is not None:
                    organization_id = session.execute(
                        sa.select(telegram_accounts.c.organization_id).where(
                            telegram_accounts.c.account_id == account_id
                        )
                    ).scalar_one_or_none()
                    if organization_id is None:
                        raise ValueError("unknown account")
                    exists = session.execute(
                        sa.select(telegram_proxies.c.proxy_id).where(
                            telegram_proxies.c.proxy_id == proxy_id,
                            sa.or_(
                                telegram_proxies.c.organization_id == organization_id,
                                telegram_proxies.c.organization_id.is_(None),
                            ),
                        )
                    ).scalar_one_or_none()
                    if exists is None:
                        raise ValueError("unknown proxy")
                row = self._override_row(session, account_id)
                if row is None:
                    session.execute(sa.insert(telegram_proxy_overrides).values(
                        account_id=account_id, proxy_id=proxy_id, revision=1
                    ))
                else:
                    session.execute(
                        sa.update(telegram_proxy_overrides)
                        .where(
                            telegram_proxy_overrides.c.account_id == account_id,
                            telegram_proxy_overrides.c.revision == row["revision"],
                        )
                        .values(proxy_id=proxy_id, revision=int(row["revision"]) + 1)
                    )
        except ValueError:
            raise
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _list_for_organization(self, organization_id: UUID) -> tuple[dict[str, object], ...]:
        try:
            with self._sessions() as session:
                rows = session.execute(
                    sa.select(
                        telegram_proxies.c.proxy_id,
                        telegram_proxies.c.endpoint,
                        telegram_proxies.c.capacity,
                        telegram_proxies.c.is_default,
                        sa.func.count(telegram_proxy_assignments.c.account_id).label("assignment_count"),
                    )
                    .outerjoin(
                        telegram_proxy_assignments,
                        telegram_proxy_assignments.c.proxy_id == telegram_proxies.c.proxy_id,
                    )
                    .where(telegram_proxies.c.organization_id == organization_id)
                    .group_by(
                        telegram_proxies.c.proxy_id,
                        telegram_proxies.c.endpoint,
                        telegram_proxies.c.capacity,
                        telegram_proxies.c.is_default,
                    )
                    .order_by(telegram_proxies.c.is_default.desc(), telegram_proxies.c.proxy_id)
                ).mappings().all()
            return tuple(
                {
                    "proxy_id": row["proxy_id"],
                    "endpoint": row["endpoint"],
                    "capacity": int(row["capacity"]),
                    "is_default": bool(row["is_default"]),
                    "assignment_count": int(row["assignment_count"]),
                    "health": "awaiting_check",
                }
                for row in rows
            )
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _reserve_assignment(self, account_id: UUID) -> ProxyReservation | None:
        try:
            with self._sessions.begin() as session:
                if self._lock_account(session, account_id) is None:
                    return None
                organization_id = session.execute(
                    sa.select(telegram_accounts.c.organization_id).where(
                        telegram_accounts.c.account_id == account_id
                    )
                ).scalar_one()
                override = self._override_row(session, account_id)
                override_proxy_id = None if override is None else override["proxy_id"]
                revision = 0 if override is None else int(override["revision"])
                if override_proxy_id is None:
                    proxy_row = session.execute(
                        sa.select(telegram_proxies)
                        .where(
                            telegram_proxies.c.is_default.is_(True),
                            sa.or_(
                                telegram_proxies.c.organization_id == organization_id,
                                telegram_proxies.c.organization_id.is_(None),
                            ),
                        )
                        .order_by(telegram_proxies.c.organization_id.is_(None), telegram_proxies.c.proxy_id)
                        .limit(1)
                        .with_for_update()
                    ).mappings().one_or_none()
                else:
                    proxy_row = session.execute(
                        sa.select(telegram_proxies)
                        .where(
                            telegram_proxies.c.proxy_id == override_proxy_id,
                            sa.or_(
                                telegram_proxies.c.organization_id == organization_id,
                                telegram_proxies.c.organization_id.is_(None),
                            ),
                        )
                        .with_for_update()
                    ).mappings().one_or_none()
                if proxy_row is None:
                    return None
                proxy = self._proxy(proxy_row)
                current = session.execute(
                    sa.select(telegram_proxy_assignments)
                    .where(telegram_proxy_assignments.c.account_id == account_id)
                    .with_for_update()
                ).mappings().one_or_none()
                if (
                    current is not None
                    and current["proxy_id"] == proxy.proxy_id
                    and int(current["assignment_revision"]) == revision
                ):
                    return ProxyReservation(
                        proxy=proxy,
                        newly_reserved=False,
                        account_override=override_proxy_id is not None,
                        assignment_id=current["assignment_id"],
                        assignment_revision=revision,
                    )
                assigned = session.execute(
                    sa.select(sa.func.count()).select_from(telegram_proxy_assignments).where(
                        telegram_proxy_assignments.c.proxy_id == proxy.proxy_id,
                        telegram_proxy_assignments.c.account_id != account_id,
                    )
                ).scalar_one()
                if assigned >= proxy.capacity:
                    return None
                assignment_id = uuid4()
                values = {
                    "proxy_id": proxy.proxy_id,
                    "assignment_id": assignment_id,
                    "assignment_revision": revision,
                    "assigned_at": _database_now(session),
                }
                if current is None:
                    session.execute(sa.insert(telegram_proxy_assignments).values(
                        account_id=account_id, **values
                    ))
                else:
                    session.execute(
                        sa.update(telegram_proxy_assignments)
                        .where(
                            telegram_proxy_assignments.c.account_id == account_id,
                            telegram_proxy_assignments.c.assignment_id == current["assignment_id"],
                        )
                        .values(**values)
                    )
                return ProxyReservation(
                    proxy=proxy,
                    newly_reserved=True,
                    account_override=override_proxy_id is not None,
                    assignment_id=assignment_id,
                    assignment_revision=revision,
                )
        except (ProxyConfigurationError, ValueError):
            raise
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _release_failed_reservation(
        self, account_id: UUID, reservation: ProxyReservation
    ) -> None:
        if not reservation.newly_reserved or reservation.account_override:
            return
        try:
            with self._sessions.begin() as session:
                self._lock_account(session, account_id)
                override = self._override_row(session, account_id)
                revision = 0 if override is None else int(override["revision"])
                if (override is not None and override["proxy_id"] is not None) or revision != reservation.assignment_revision:
                    return
                session.execute(
                    sa.delete(telegram_proxy_assignments).where(
                        telegram_proxy_assignments.c.account_id == account_id,
                        telegram_proxy_assignments.c.proxy_id == reservation.proxy.proxy_id,
                        telegram_proxy_assignments.c.assignment_id == reservation.assignment_id,
                        telegram_proxy_assignments.c.assignment_revision == reservation.assignment_revision,
                    )
                )
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _release_terminal_assignment(self, account_id: UUID) -> None:
        try:
            with self._sessions.begin() as session:
                self._lock_account(session, account_id)
                override = self._override_row(session, account_id)
                if override is None or override["proxy_id"] is None:
                    session.execute(
                        sa.delete(telegram_proxy_assignments).where(
                            telegram_proxy_assignments.c.account_id == account_id
                        )
                    )
        except Exception:
            raise TelegramStateRepositoryUnavailable() from None

    def _proxy(self, row) -> ProxyConfig:
        username, password = self._credential_cipher.decrypt(
            proxy_id=row["proxy_id"],
            endpoint=row["endpoint"],
            key_version=row["credential_key_version"],
            ciphertext=row["credential_ciphertext"],
        )
        values: dict[str, object] = {
            "proxy_id": row["proxy_id"],
            "endpoint": row["endpoint"],
            "capacity": int(row["capacity"]),
        }
        if username is not None:
            values["username"] = username
        if password is not None:
            values["password"] = password
        return ProxyConfig(**values)

    @staticmethod
    def _override_row(session: Session, account_id: UUID):
        return session.execute(
            sa.select(telegram_proxy_overrides)
            .where(telegram_proxy_overrides.c.account_id == account_id)
            .with_for_update()
        ).mappings().one_or_none()

    @staticmethod
    def _lock_account(session: Session, account_id: UUID):
        return session.execute(
            sa.select(telegram_accounts.c.account_id)
            .where(telegram_accounts.c.account_id == account_id)
            .with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _locked_account(session: Session, account_id: UUID):
        return session.execute(
            sa.select(telegram_accounts.c.account_id, telegram_accounts.c.organization_id)
            .where(telegram_accounts.c.account_id == account_id)
            .with_for_update()
        ).mappings().one_or_none()

    @staticmethod
    def _locked_proxy_row(session: Session, proxy_id: UUID):
        return session.execute(
            sa.select(telegram_proxies)
            .where(telegram_proxies.c.proxy_id == proxy_id)
            .with_for_update()
        ).mappings().one_or_none()


def _organization_filter(column, organization_id: UUID | None):
    return column.is_(None) if organization_id is None else column == organization_id


def _stored_session(row) -> StoredSessionCiphertext:
    return StoredSessionCiphertext(
        account_id=row["account_id"],
        session_id=row["session_id"],
        key_version=int(row["key_version"]),
        ciphertext=bytes(row["ciphertext"]),
    )


def _connection_record(row) -> ConnectionRecord:
    return ConnectionRecord(
        account_id=row["account_id"],
        session_ref=SessionRef(
            account_id=row["account_id"],
            session_id=row["session_id"],
            key_version=int(row["key_version"]),
        ),
        health=ConnectionHealth(
            state=row["state"],
            last_seen_at=_utc(row["last_seen_at"]),
            proxy_ip=row["proxy_ip"],
            latency_ms=row["latency_ms"],
            error_code=row["error_code"],
        ),
        retry_count=int(row["retry_count"]),
        retry_at=_utc(row["retry_at"]),
        version=int(row["version"]),
        lease_owner_id=row["lease_owner_id"],
        lease_expires_at=_utc(row["lease_expires_at"]),
        fence_token=int(row["fence_token"]),
    )


def _connection_values(record: ConnectionRecord) -> dict[str, object]:
    _validate_connection_record(record)
    return {
        "account_id": record.account_id,
        "session_id": record.session_ref.session_id,
        "key_version": record.session_ref.key_version,
        "state": record.health.state,
        "last_seen_at": record.health.last_seen_at,
        "proxy_ip": record.health.proxy_ip,
        "latency_ms": record.health.latency_ms,
        "error_code": record.health.error_code,
        "retry_count": record.retry_count,
        "retry_at": record.retry_at,
        "version": record.version,
        "lease_owner_id": record.lease_owner_id,
        "lease_expires_at": record.lease_expires_at,
        "fence_token": record.fence_token,
    }


def _validate_connection_record(record: ConnectionRecord) -> None:
    if record.account_id != record.session_ref.account_id:
        raise ValueError("session reference account mismatch")


def _claim_matches(
    current: ConnectionRecord,
    candidate: ConnectionRecord,
    owner_id: UUID,
    now: datetime | None,
    *,
    require_live: bool,
) -> bool:
    return (
        current.version == candidate.version
        and current.lease_owner_id == owner_id
        and current.fence_token == candidate.fence_token
        and (
            not require_live
            or (
                current.lease_expires_at is not None
                and now is not None
                and current.lease_expires_at > now
            )
        )
        and current.health.state != "archived"
    )


def _database_now(session: Session) -> datetime:
    value = session.execute(sa.select(sa.func.current_timestamp())).scalar_one()
    normalized = _utc(value)
    if normalized is None:
        raise TelegramStateRepositoryUnavailable()
    return normalized


def _duration(seconds: float) -> timedelta:
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("invalid lease duration")
    return timedelta(seconds=seconds)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _proxy_aad(proxy_id: UUID, endpoint: str, key_version: int) -> bytes:
    endpoint_digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
    return f"{proxy_id}:{key_version}:{endpoint_digest}".encode("ascii")
