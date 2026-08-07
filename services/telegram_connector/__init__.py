"""Test-only Telegram connector primitives."""

from .adapters.base import SessionAdapter, SessionMaterial, SessionProbeResult
from .config import ConnectorSettings
from .quarantine import QuarantinedUpload, SessionQuarantineProcessor, UnsafeQuarantinedUpload
from .session_store import (
    EncryptedSessionStore,
    SessionCiphertextAuthenticationError,
    SessionRef,
    StoredSessionCiphertext,
)

__all__ = [
    "ConnectorSettings",
    "EncryptedSessionStore",
    "QuarantinedUpload",
    "SessionAdapter",
    "SessionCiphertextAuthenticationError",
    "SessionMaterial",
    "SessionProbeResult",
    "SessionQuarantineProcessor",
    "SessionRef",
    "StoredSessionCiphertext",
    "UnsafeQuarantinedUpload",
]
