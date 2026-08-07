"""Test-only Telegram connector primitives."""

from .adapters.base import SessionAdapter, SessionMaterial, SessionProbeResult
from .config import ConnectorSettings
from .quarantine import QuarantinedUpload, SessionQuarantineProcessor
from .session_store import EncryptedSessionStore, SessionRef, StoredSessionCiphertext

__all__ = [
    "ConnectorSettings",
    "EncryptedSessionStore",
    "QuarantinedUpload",
    "SessionAdapter",
    "SessionMaterial",
    "SessionProbeResult",
    "SessionQuarantineProcessor",
    "SessionRef",
    "StoredSessionCiphertext",
]
