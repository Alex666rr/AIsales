"""One-time, owner-bound tickets for a local tdata conversion handoff."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from telegram_connector.importers.tdata.handoff import (
    decode_handoff_payload,
    encode_handoff_payload,
)

encode_tdata_handoff = encode_handoff_payload
decode_tdata_handoff = decode_handoff_payload


class TicketRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("tdata ticket rejected")


@dataclass(frozen=True, slots=True)
class TdataTicket:
    ticket_id: UUID
    expires_at: datetime
    public_key: str


@dataclass(slots=True)
class _TicketRecord:
    owner_id: UUID
    expires_at: datetime
    private_key: X25519PrivateKey


class TdataTicketRegistry:
    """Bounded ephemeral registry; ticket state never contains raw tdata."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("invalid tdata ticket ttl")
        self._now = now
        self._ttl = ttl
        self._records: dict[UUID, _TicketRecord] = {}
        self._lock = asyncio.Lock()

    async def issue(self, owner_id: UUID) -> TdataTicket:
        now = self._utc_now()
        async with self._lock:
            self._prune_locked(now)
            ticket_id = uuid4()
            expiry = now + self._ttl
            private_key = X25519PrivateKey.generate()
            public_key = base64.urlsafe_b64encode(private_key.public_key().public_bytes_raw()).decode().rstrip("=")
            self._records[ticket_id] = _TicketRecord(owner_id=owner_id, expires_at=expiry, private_key=private_key)
            return TdataTicket(ticket_id=ticket_id, expires_at=expiry, public_key=public_key)

    async def consume(self, owner_id: UUID, ticket_id: UUID) -> None:
        now = self._utc_now()
        async with self._lock:
            self._prune_locked(now)
            record = self._records.get(ticket_id)
            if record is None or record.owner_id != owner_id or record.expires_at <= now:
                raise TicketRejected()
            self._records.pop(ticket_id, None)

    async def consume_envelope(
        self, owner_id: UUID, ticket_id: UUID, client_public_key: bytes, nonce: bytes, ciphertext: bytes
    ) -> bytes:
        now = self._utc_now()
        async with self._lock:
            self._prune_locked(now)
            record = self._records.get(ticket_id)
            if record is None or record.owner_id != owner_id or record.expires_at <= now:
                raise TicketRejected()
            self._records.pop(ticket_id, None)
        try:
            if type(client_public_key) is not bytes or len(client_public_key) != 32:
                raise ValueError
            if type(nonce) is not bytes or len(nonce) != 12:
                raise ValueError
            if type(ciphertext) is not bytes or not 16 <= len(ciphertext) <= 8192:
                raise ValueError
            shared = record.private_key.exchange(X25519PublicKey.from_public_bytes(client_public_key))
            key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"ai-sales/tdata-ticket/" + ticket_id.bytes).derive(shared)
            return AESGCM(key).decrypt(nonce, ciphertext, ticket_id.bytes)
        except Exception:
            raise TicketRejected() from None

    def _prune_locked(self, now: datetime) -> None:
        for ticket_id, record in tuple(self._records.items()):
            if record.expires_at <= now:
                self._records.pop(ticket_id, None)

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ticket clock must be timezone-aware")
        return value.astimezone(UTC)
