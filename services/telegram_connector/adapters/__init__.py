"""Adapter contracts and safe authorization adapters for the Telegram connector."""

from .base import SessionAdapter, SessionMaterial, SessionProbeResult
from .bot import BotAdapter
from .concrete import (
    DefaultDenyTDataConverter,
    TelegramBotApiClient,
    TelethonAuthorizationClientFactory,
    TelethonClientAdapter,
    TelethonRuntimeClientFactory,
    VettedTelethonSessionConverter,
)
from .phone import AuthStep, PhoneAdapter
from .qr import QRAdapter
from .registry import AdapterRegistry
from .tdata import TDataAdapter
from .telethon_session import TelethonFileAdapter, TelethonStringAdapter

__all__ = [
    "AdapterRegistry",
    "AuthStep",
    "BotAdapter",
    "DefaultDenyTDataConverter",
    "PhoneAdapter",
    "QRAdapter",
    "SessionAdapter",
    "SessionMaterial",
    "SessionProbeResult",
    "TDataAdapter",
    "TelegramBotApiClient",
    "TelethonAuthorizationClientFactory",
    "TelethonClientAdapter",
    "TelethonFileAdapter",
    "TelethonStringAdapter",
    "TelethonRuntimeClientFactory",
    "VettedTelethonSessionConverter",
]
