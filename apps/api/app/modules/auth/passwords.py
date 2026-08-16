"""One-way credential derivation and verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


_SCRYPT_PREFIX = "scrypt-v1"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    """Return a salted, one-way password representation."""
    return _hash_secret(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Return false for malformed stored values rather than leaking parser details."""
    return _verify_secret(password, stored_hash)


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code independently before persistence."""
    return _hash_secret(code)


def verify_recovery_code(code: str, stored_hash: str) -> bool:
    return _verify_secret(code, stored_hash)


def _hash_secret(secret: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return "$".join(
        (
            _SCRYPT_PREFIX,
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            _encode(salt),
            _encode(derived),
        )
    )


def _verify_secret(secret: str, stored_hash: str) -> bool:
    try:
        prefix, n, r, p, salt_value, expected_value = stored_hash.split("$")
        if prefix != _SCRYPT_PREFIX:
            return False
        derived = hashlib.scrypt(
            secret.encode("utf-8"),
            salt=_decode(salt_value),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(derived, _decode(expected_value))
    except (ValueError, TypeError):
        return False


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
