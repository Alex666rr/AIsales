from __future__ import annotations

import asyncio
import base64
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.modules.policy.models import PlatformOwnerPrincipal
from app.modules.telegram_connections.tdata_handoff import TdataHandoffService
from app.modules.telegram_connections.tdata_ticket import TdataTicketRegistry, encode_tdata_handoff
from telegram_connector.runtime.connection import InMemoryConnectionRepository
from telegram_connector.session_store import EncryptedSessionStore


class FakeAccountProvisioner:
    def __init__(self) -> None:
        self.account_id = uuid4()
        self.calls: list[tuple[UUID, int]] = []

    async def provision(self, organization_id: UUID, telegram_user_id: int) -> UUID:
        self.calls.append((organization_id, telegram_user_id))
        return self.account_id


def test_tdata_handoff_decrypts_once_and_persists_only_encrypted_session() -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        principal = PlatformOwnerPrincipal(principal_id=owner_id)
        tickets = TdataTicketRegistry()
        accounts = FakeAccountProvisioner()
        store = EncryptedSessionStore.test_store({1: b"k" * 32}, active_key_version=1)
        connections = InMemoryConnectionRepository()
        service = TdataHandoffService(
            tickets=tickets,
            accounts=accounts,
            sessions=store,
            connections=connections,
        )
        ticket = await tickets.issue(owner_id)
        client_private = X25519PrivateKey.generate()
        server_public = X25519PublicKey.from_public_bytes(
            base64.urlsafe_b64decode(ticket.public_key + "==")
        )
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"ai-sales/tdata-ticket/" + ticket.ticket_id.bytes,
        ).derive(client_private.exchange(server_public))
        raw_session = b"TELETHON_STRING_SESSION\x00\x01must-never-persist-in-plaintext"
        nonce = b"n" * 12
        ciphertext = AESGCM(key).encrypt(
            nonce,
            encode_tdata_handoff(telegram_user_id=123456, session_payload=raw_session),
            ticket.ticket_id.bytes,
        )

        result = await service.accept(
            principal,
            ticket.ticket_id,
            client_private.public_key().public_bytes_raw(),
            nonce,
            ciphertext,
        )

        assert result.account_id == accounts.account_id
        assert result.telegram_user_id == 123456
        assert result.state == "quarantine"
        assert accounts.calls == [(owner_id, 123456)]
        assert b"must-never-persist-in-plaintext" not in store.persisted_records()[0].ciphertext
        assert (await connections.get(accounts.account_id)).health.state == "quarantine"

    asyncio.run(scenario())
