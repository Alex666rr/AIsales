"""Persist one verified session before allowing a Telegram runtime to start."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from telegram_connector.runtime.connection import ConnectionHealth, ConnectionRecord
from telegram_connector.session_store import EncryptedSessionStore, SessionRef


class ConnectionUnavailable(RuntimeError):
    """Safe failure for unavailable connection persistence or runtime startup."""

    def __init__(self) -> None:
        super().__init__("telegram connection unavailable")


class AccountProvisioner(Protocol):
    async def provision(self, organization_id: UUID, telegram_user_id: int) -> UUID: ...


class ConnectionWriter(Protocol):
    async def save(self, record: ConnectionRecord) -> None: ...


class ConnectionStarter(Protocol):
    async def start(self, account_id: UUID) -> ConnectionHealth: ...


@dataclass(frozen=True, slots=True)
class ConnectedAccountView:
    account_id: UUID
    telegram_user_id: int
    health: ConnectionHealth


class ConnectionFinalizer:
    """Order operations so the runtime cannot use a session before it is encrypted and stored."""

    def __init__(
        self,
        *,
        accounts: AccountProvisioner,
        sessions: EncryptedSessionStore,
        connections: ConnectionWriter,
        supervisor: ConnectionStarter,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._accounts = accounts
        self._sessions = sessions
        self._connections = connections
        self._supervisor = supervisor
        self._now = now

    async def finalize(
        self,
        *,
        organization_id: UUID,
        telegram_user_id: int,
        session_payload: bytes,
    ) -> ConnectedAccountView:
        if telegram_user_id <= 0 or not session_payload:
            raise ConnectionUnavailable()
        try:
            account_id = await self._accounts.provision(organization_id, telegram_user_id)
            session_ref = self._sessions.put(account_id, session_payload)
            await self._connections.save(self._quarantined_record(account_id, session_ref))
        except Exception as error:
            raise ConnectionUnavailable() from error
        try:
            health = await self._supervisor.start(account_id)
        except Exception as error:
            raise ConnectionUnavailable() from error
        return ConnectedAccountView(
            account_id=account_id,
            telegram_user_id=telegram_user_id,
            health=health,
        )

    def _quarantined_record(self, account_id: UUID, session_ref: SessionRef) -> ConnectionRecord:
        timestamp = self._now()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("connection clock must be timezone-aware")
        return ConnectionRecord(
            account_id=account_id,
            session_ref=session_ref,
            health=ConnectionHealth(
                state="quarantine",
                last_seen_at=timestamp.astimezone(UTC),
                proxy_ip=None,
                latency_ms=None,
                error_code=None,
            ),
        )
