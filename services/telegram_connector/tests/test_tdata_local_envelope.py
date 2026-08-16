from __future__ import annotations

import base64
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from telegram_connector.importers.tdata.handoff import decrypt_handoff_for_test, encrypt_handoff


def test_local_handoff_encrypts_canonical_session_for_only_the_issued_ticket_key() -> None:
    ticket_id = uuid4()
    server_private = X25519PrivateKey.generate()
    session = b"TELETHON_STRING_SESSION\x00\x01local-only-before-handoff"

    envelope = encrypt_handoff(
        ticket_id=ticket_id,
        server_public_key=base64.urlsafe_b64encode(
            server_private.public_key().public_bytes_raw()
        ).decode().rstrip("="),
        telegram_user_id=123456,
        session_payload=session,
    )

    assert decrypt_handoff_for_test(
        ticket_id=ticket_id,
        server_private_key=server_private,
        envelope=envelope,
    ) == (123456, session)
    assert b"local-only-before-handoff" not in envelope.ciphertext.encode()
