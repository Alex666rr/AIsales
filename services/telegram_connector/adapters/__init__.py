"""Adapter contracts and safe authorization adapters for the Telegram connector."""

from .base import SessionAdapter, SessionMaterial, SessionProbeResult
from .bot import BotAdapter
from .phone import AuthStep, PhoneAdapter
from .qr import QRAdapter
from .registry import AdapterRegistry
from .tdata import TDataAdapter
from .telethon_session import TelethonFileAdapter, TelethonStringAdapter

__all__ = [
    "AdapterRegistry",
    "AuthStep",
    "BotAdapter",
    "PhoneAdapter",
    "QRAdapter",
    "SessionAdapter",
    "SessionMaterial",
    "SessionProbeResult",
    "TDataAdapter",
    "TelethonFileAdapter",
    "TelethonStringAdapter",
]
