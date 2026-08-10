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
from .compatibility import CompatibilityRecord, CompatibilityRegistry
from .error_codes import TelegramGatewayError, map_telegram_error
from .gateway import (
    DeliveryRecord,
    DeliveryResult,
    InMemoryMessageDeliveryRepository,
    IncomingTelegramEvent,
    MessageCommand,
    TelegramGateway,
    TelegramUpdate,
)
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
    "CompatibilityRecord",
    "CompatibilityRegistry",
    "DeliveryRecord",
    "DeliveryResult",
    "AdapterRegistry",
    "AuthStep",
    "BotAdapter",
    "EncryptedSessionStore",
    "InMemoryMessageDeliveryRepository",
    "IncomingTelegramEvent",
    "MessageCommand",
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
    "TelegramGateway",
    "TelegramGatewayError",
    "TelegramUpdate",
    "map_telegram_error",
]
