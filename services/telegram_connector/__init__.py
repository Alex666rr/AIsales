"""Test-only Telegram connector primitives."""

from .adapters import (
    AdapterRegistry,
    AuthStep,
    BotAdapter,
    PhoneAdapter,
    QRAdapter,
    SessionAdapter,
    SessionMaterial,
    SessionProbeResult,
    TDataAdapter,
    TelethonFileAdapter,
    TelethonStringAdapter,
)
from .config import ConnectorSettings
from .quarantine import QuarantinedUpload, SessionQuarantineProcessor, UnsafeQuarantinedUpload
from .session_store import (
    EncryptedSessionStore,
    SessionCiphertextAuthenticationError,
    SessionRef,
    StoredSessionCiphertext,
)
from .proxies import ProxyConfig, ProxyHealth
from .runtime.connection import ConnectionHealth
from .runtime.supervisor import ConnectionSupervisor

__all__ = [
    "ConnectorSettings",
    "AdapterRegistry",
    "AuthStep",
    "BotAdapter",
    "EncryptedSessionStore",
    "QuarantinedUpload",
    "PhoneAdapter",
    "QRAdapter",
    "SessionAdapter",
    "SessionCiphertextAuthenticationError",
    "SessionMaterial",
    "SessionProbeResult",
    "SessionQuarantineProcessor",
    "SessionRef",
    "StoredSessionCiphertext",
    "TDataAdapter",
    "TelethonFileAdapter",
    "TelethonStringAdapter",
    "UnsafeQuarantinedUpload",
    "ConnectionHealth",
    "ConnectionSupervisor",
    "ProxyConfig",
    "ProxyHealth",
]
