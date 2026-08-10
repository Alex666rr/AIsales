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
from .error_codes import (
    InvalidPeerAdapterError,
    PaidMessageRequiredAdapterError,
    PrivacyRestrictedAdapterError,
    TelegramGatewayError,
    map_telegram_error,
)
from .gateway import (
    ApprovedAdapterRegistry,
    DeliveryRecord,
    DeliveryResult,
    InMemoryMessageDeliveryRepository,
    MessageCommand,
    TelegramGateway,
    TelegramUpdate,
)
from .persistence import (
    SqlAlchemyCompatibilityRegistry,
    SqlAlchemyMessageDeliveryRepository,
    create_gateway_schema,
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
    "ApprovedAdapterRegistry",
    "AuthStep",
    "BotAdapter",
    "EncryptedSessionStore",
    "InMemoryMessageDeliveryRepository",
    "InvalidPeerAdapterError",
    "MessageCommand",
    "PaidMessageRequiredAdapterError",
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
    "PrivacyRestrictedAdapterError",
    "SqlAlchemyCompatibilityRegistry",
    "SqlAlchemyMessageDeliveryRepository",
    "TelegramGateway",
    "TelegramGatewayError",
    "TelegramUpdate",
    "create_gateway_schema",
    "map_telegram_error",
]
