"""Encrypted TOTP storage and verification without returning plaintext secrets."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_totp_secret() -> bytes:
    """Generate the 160-bit secret material used by a TOTP authenticator."""
    return os.urandom(20)


def enrollment_uri(*, secret: bytes, email: str, issuer: str = "AIsales") -> str:
    """Build a scan-only otpauth URI; callers must not persist or log it."""
    encoded_secret = base64.b32encode(secret).decode("ascii").rstrip("=")
    label = quote(f"{issuer}:{email}", safe=":")
    query = urlencode({"secret": encoded_secret, "issuer": issuer, "algorithm": "SHA1", "digits": "6", "period": "30"})
    return f"otpauth://totp/{label}?{query}"


def encrypt_totp_secret(secret: bytes, encryption_key: bytes) -> str:
    """Encrypt a TOTP secret for database storage."""
    nonce = os.urandom(12)
    ciphertext = AESGCM(encryption_key).encrypt(nonce, secret, b"auth-totp-v1")
    return _encode(nonce + ciphertext)


def decrypt_totp_secret(envelope: str, encryption_key: bytes) -> bytes:
    """Decrypt only inside the authentication service's verification path."""
    decoded = _decode(envelope)
    if len(decoded) <= 12:
        raise ValueError("invalid encrypted TOTP secret")
    return AESGCM(encryption_key).decrypt(decoded[:12], decoded[12:], b"auth-totp-v1")


def totp_at(secret: bytes, moment: datetime) -> str:
    """Compute an RFC 6238 compatible six-digit TOTP value."""
    timestamp = int(moment.astimezone(UTC).timestamp())
    counter = timestamp // 30
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFF_FFFF
    return f"{binary % 1_000_000:06d}"


def verify_totp(secret: bytes, code: str, moment: datetime) -> bool:
    return hmac.compare_digest(totp_at(secret, moment), code)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
