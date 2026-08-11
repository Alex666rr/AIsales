"""Network-free tests for reviewed concrete Telegram boundaries."""

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from uuid import UUID

import pytest

from telegram_connector.adapters.base import SessionMaterial
from telegram_connector.adapters.bot import BotAdapter
from telegram_connector.adapters.concrete import (
    DefaultDenyTDataConverter,
    TelegramBotApiClient,
    TelethonRuntimeClientFactory,
    VettedTelethonSessionConverter,
)
from telegram_connector.proxies import ProxyConfig
from telegram_connector.session_store import EncryptedSessionStore


class RecordingCodec:
    def canonicalize_string(self, value: str) -> str:
        if value != "owned-test-session":
            raise ValueError
        return "canonical-owned-session"

    def sqlite_to_string(self, path) -> str:
        assert path.read_bytes().startswith(b"SQLite format 3\x00")
        return "canonical-file-session"


def test_vetted_converter_normalizes_string_and_sqlite_sessions_without_network():
    """Bypassing the codec validation would persist arbitrary upload bytes as a runnable session."""

    async def scenario():
        converter = VettedTelethonSessionConverter(RecordingCodec())
        string_result = await converter.convert_telethon_string(b"owned-test-session")
        file_result = await converter.convert_telethon_file(b"SQLite format 3\x00" + b"fixture")

        assert string_result.endswith(b"canonical-owned-session")
        assert file_result.endswith(b"canonical-file-session")
        assert string_result.startswith(b"TELETHON_STRING_SESSION\x00\x01")
        with pytest.raises(ValueError, match="^unsupported session import$"):
            await converter.convert_telethon_string(b"RAW-SESSION-SENTINEL")

    asyncio.run(scenario())


def test_default_tdata_composition_fails_closed_without_an_unreviewed_converter():
    """Production must not guess how to transform a TData archive through an unvetted dependency."""
    with pytest.raises(ValueError, match="^unsupported session import$"):
        asyncio.run(DefaultDenyTDataConverter().convert_tdata(b"RAW-TDATA-SENTINEL"))


def test_runtime_factory_builds_the_concrete_telethon_adapter_with_injected_modules(monkeypatch):
    """The real factory must consume encrypted session bytes and a fixed proxy without live network calls."""
    constructed = []

    class FakeStringSession:
        def __init__(self, value="") -> None:
            self.value = value

    class FakeTelegramClient:
        def __init__(self, session, api_id, api_hash, **options) -> None:
            constructed.append((session.value, api_id, api_hash, options))
            self.connected = False

        async def connect(self):
            self.connected = True

        async def is_user_authorized(self):
            return True

        async def disconnect(self):
            self.connected = False

        async def send_message(self, peer_id, body):
            return SimpleNamespace(id=91)

    telethon = ModuleType("telethon")
    telethon.TelegramClient = FakeTelegramClient
    sessions = ModuleType("telethon.sessions")
    sessions.StringSession = FakeStringSession
    monkeypatch.setitem(sys.modules, "telethon", telethon)
    monkeypatch.setitem(sys.modules, "telethon.sessions", sessions)

    async def scenario():
        store = EncryptedSessionStore.test_store({1: b"k" * 32}, active_key_version=1)
        reference = store.put(
            UUID(int=1),
            b"TELETHON_STRING_SESSION\x00\x01canonical-owned-session",
        )
        factory = TelethonRuntimeClientFactory(
            store, api_id=12345, api_hash="API-HASH-SENTINEL"
        )
        client = await factory.create(
            reference,
            ProxyConfig(
                proxy_id=UUID(int=2),
                url="socks5://proxy-user:proxy-password@edge.example:1080",
            ),
        )
        await client.connect()
        assert await client.is_authorized() is True
        assert await client.send_message(77, "synthetic test body", "fixed-key") == "91"
        await client.disconnect()

        value, api_id, api_hash, options = constructed[0]
        assert (value, api_id, api_hash) == (
            "canonical-owned-session",
            12345,
            "API-HASH-SENTINEL",
        )
        assert options["proxy"] == {
            "proxy_type": "socks5",
            "addr": "edge.example",
            "port": 1080,
            "rdns": True,
            "username": "proxy-user",
            "password": "proxy-password",
        }

    asyncio.run(scenario())


def test_bot_api_adapter_redacts_a_secret_bearing_transport_failure():
    """A URL-bearing HTTP failure must normalize to invalid authorization without exposing the token."""
    token = "12345:BOT-TOKEN-SENTINEL"

    def failing_opener(request, *, timeout):
        raise RuntimeError(request.full_url)

    material = SessionMaterial(adapter="bot", payload=b"", credentials={"token": token})
    result = asyncio.run(BotAdapter(TelegramBotApiClient(opener=failing_opener)).probe(material))

    assert result.state == "invalid"
    assert result.error_code == "invalid_bot_token"
    assert token not in repr(result)
