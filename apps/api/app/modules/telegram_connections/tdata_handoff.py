"""Persist a local tdata conversion only after one-time envelope decryption."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.modules.policy.models import PlatformOwnerPrincipal
from telegram_connector.runtime.connection import ConnectionHealth, ConnectionRecord
from telegram_connector.session_store import EncryptedSessionStore, SessionRef

from .models import TdataConnectionView
from .tdata_ticket import TdataTicketRegistry, decode_tdata_handoff


class AccountProvisioner(Protocol):
    async def provision(self, organization_id: UUID, telegram_user_id: int) -> UUID: ...


class ConnectionWriter(Protocol):
    async def save(self, record: ConnectionRecord) -> None: ...


class TdataHandoffService:
    """The server never sees raw tdata; it only receives one encrypted session envelope."""

    def __init__(
        self,
        *,
        tickets: TdataTicketRegistry,
        accounts: AccountProvisioner,
        sessions: EncryptedSessionStore,
        connections: ConnectionWriter,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._tickets = tickets
        self._accounts = accounts
        self._sessions = sessions
        self._connections = connections
        self._now = now

    async def accept(
        self,
        owner: PlatformOwnerPrincipal,
        ticket_id: UUID,
        client_public_key: bytes,
        nonce: bytes,
        ciphertext: bytes,
    ) -> TdataConnectionView:
        payload = await self._tickets.consume_envelope(
            owner.principal_id, ticket_id, client_public_key, nonce, ciphertext
        )
        telegram_user_id, session_payload = decode_tdata_handoff(payload)
        account_id = await self._accounts.provision(owner.principal_id, telegram_user_id)
        session_ref = self._sessions.put(account_id, session_payload)
        await self._connections.save(self._quarantine_record(account_id, session_ref))
        return TdataConnectionView(
            account_id=account_id,
            telegram_user_id=telegram_user_id,
            state="quarantine",
        )

    def _quarantine_record(self, account_id: UUID, session_ref: SessionRef) -> ConnectionRecord:
        timestamp = self._now()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("tdata handoff clock must be timezone-aware")
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
