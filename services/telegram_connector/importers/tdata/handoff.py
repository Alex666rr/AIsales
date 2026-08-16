"""Local-only envelope format for handing a converted tdata session to the API."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


_HANDOFF_PREFIX = b"AI-SALES-TDATA-HANDOFF\x00\x01"
_SESSION_PREFIX = b"TELETHON_STRING_SESSION\x00\x01"


@dataclass(frozen=True, slots=True)
class TdataHandoffEnvelope:
    client_public_key: str
    nonce: str
    ciphertext: str


def encode_handoff_payload(*, telegram_user_id: int, session_payload: bytes) -> bytes:
    if type(telegram_user_id) is not int or telegram_user_id <= 0:
        raise ValueError("invalid local tdata identity")
    if type(session_payload) is not bytes or not session_payload.startswith(_SESSION_PREFIX):
        raise ValueError("invalid local tdata session")
    if not len(_SESSION_PREFIX) < len(session_payload) <= 4096:
        raise ValueError("invalid local tdata session")
    return _HANDOFF_PREFIX + telegram_user_id.to_bytes(8, "big") + session_payload


def decode_handoff_payload(payload: bytes) -> tuple[int, bytes]:
    if type(payload) is not bytes or len(payload) <= len(_HANDOFF_PREFIX) + 8:
        raise ValueError("invalid tdata handoff")
    if not payload.startswith(_HANDOFF_PREFIX):
        raise ValueError("invalid tdata handoff")
    telegram_user_id = int.from_bytes(payload[len(_HANDOFF_PREFIX) : len(_HANDOFF_PREFIX) + 8], "big")
    session_payload = payload[len(_HANDOFF_PREFIX) + 8 :]
    if telegram_user_id <= 0 or not session_payload.startswith(_SESSION_PREFIX):
        raise ValueError("invalid tdata handoff")
    if not len(_SESSION_PREFIX) < len(session_payload) <= 4096:
        raise ValueError("invalid tdata handoff")
    return telegram_user_id, session_payload


def encrypt_handoff(
    *, ticket_id: UUID, server_public_key: str, telegram_user_id: int, session_payload: bytes
) -> TdataHandoffEnvelope:
    """Encrypt only locally validated session material for a one-time API ticket."""
    try:
        server_key = X25519PublicKey.from_public_bytes(_decode(server_public_key))
        client_key = X25519PrivateKey.generate()
        shared = client_key.exchange(server_key)
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"ai-sales/tdata-ticket/" + ticket_id.bytes,
        ).derive(shared)
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(
            nonce,
            encode_handoff_payload(
                telegram_user_id=telegram_user_id, session_payload=session_payload
            ),
            ticket_id.bytes,
        )
        return TdataHandoffEnvelope(
            client_public_key=_encode(client_key.public_key().public_bytes_raw()),
            nonce=_encode(nonce),
            ciphertext=_encode(ciphertext),
        )
    except ValueError:
        raise
    except Exception:
        raise ValueError("invalid tdata handoff ticket") from None


def decrypt_handoff_for_test(
    *, ticket_id: UUID, server_private_key: X25519PrivateKey, envelope: TdataHandoffEnvelope
) -> tuple[int, bytes]:
    """Test helper proving the envelope has no plaintext session field."""
    shared = server_private_key.exchange(X25519PublicKey.from_public_bytes(_decode(envelope.client_public_key)))
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=b"ai-sales/tdata-ticket/" + ticket_id.bytes,
    ).derive(shared)
    return decode_handoff_payload(
        AESGCM(key).decrypt(_decode(envelope.nonce), _decode(envelope.ciphertext), ticket_id.bytes)
    )


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid tdata handoff ticket")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except Exception:
        raise ValueError("invalid tdata handoff ticket") from None
