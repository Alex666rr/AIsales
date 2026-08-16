from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.modules.telegram_connections.tdata_ticket import TdataTicketRegistry, TicketRejected


OWNER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OWNER_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def test_ticket_is_owner_bound_one_time_and_expires() -> None:
    """A tdata upload must never be replayable or transferable between owners."""

    async def scenario() -> None:
        clock = [datetime(2026, 8, 11, tzinfo=UTC)]
        registry = TdataTicketRegistry(now=lambda: clock[0], ttl=timedelta(minutes=1))
        ticket = await registry.issue(OWNER_A)

        with pytest.raises(TicketRejected, match="^tdata ticket rejected$"):
            await registry.consume(OWNER_B, ticket.ticket_id)
        await registry.consume(OWNER_A, ticket.ticket_id)
        with pytest.raises(TicketRejected, match="^tdata ticket rejected$"):
            await registry.consume(OWNER_A, ticket.ticket_id)

        expired = await registry.issue(OWNER_A)
        clock[0] += timedelta(minutes=2)
        with pytest.raises(TicketRejected, match="^tdata ticket rejected$"):
            await registry.consume(OWNER_A, expired.ticket_id)

    asyncio.run(scenario())


def test_ticket_decrypts_only_one_client_envelope() -> None:
    """Only a local client holding the ticket public key may hand off the session bytes."""

    async def scenario() -> None:
        registry = TdataTicketRegistry()
        ticket = await registry.issue(OWNER_A)
        client_private = X25519PrivateKey.generate()
        server_public = X25519PublicKey.from_public_bytes(
            base64.urlsafe_b64decode(ticket.public_key + "==")
        )
        shared = client_private.exchange(server_public)
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None,
            info=b"ai-sales/tdata-ticket/" + ticket.ticket_id.bytes,
        ).derive(shared)
        nonce = b"n" * 12
        payload = b"TELETHON_STRING_SESSION\x00\x01local-session"
        ciphertext = AESGCM(key).encrypt(nonce, payload, ticket.ticket_id.bytes)
        client_public = client_private.public_key().public_bytes_raw()

        result = await registry.consume_envelope(
            OWNER_A, ticket.ticket_id, client_public, nonce, ciphertext
        )

        assert result == payload
        with pytest.raises(TicketRejected, match="^tdata ticket rejected$"):
            await registry.consume_envelope(OWNER_A, ticket.ticket_id, client_public, nonce, ciphertext)

    asyncio.run(scenario())
