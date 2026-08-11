"""Reviewed concrete Telegram boundaries with lazy third-party imports."""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from telegram_connector.proxies import ProxyConfig
from telegram_connector.session_store import EncryptedSessionStore, SessionRef


_SESSION_PREFIX = b"TELETHON_STRING_SESSION\x00\x01"
_SAFE_CONVERSION_ERROR = "unsupported session import"


class TelethonSessionCodec(Protocol):
    """Offline codec seam retained for deterministic converter tests."""

    def canonicalize_string(self, value: str) -> str: ...

    def sqlite_to_string(self, path: Path) -> str: ...


class _ImportedTelethonSessionCodec:
    def canonicalize_string(self, value: str) -> str:
        try:
            from telethon.sessions import StringSession

            return StringSession.save(StringSession(value))
        except Exception:
            raise ValueError(_SAFE_CONVERSION_ERROR) from None

    def sqlite_to_string(self, path: Path) -> str:
        try:
            from telethon.sessions import SQLiteSession, StringSession

            session = SQLiteSession(str(path))
            try:
                return StringSession.save(session)
            finally:
                session.close()
        except Exception:
            raise ValueError(_SAFE_CONVERSION_ERROR) from None


class VettedTelethonSessionConverter:
    """Convert validated Telethon inputs to one canonical encrypted-store envelope."""

    def __init__(self, codec: TelethonSessionCodec | None = None) -> None:
        self._codec = codec or _ImportedTelethonSessionCodec()

    async def convert_telethon_string(self, data: bytes) -> bytes:
        try:
            if not 1 <= len(data) <= 4096:
                raise ValueError
            value = data.decode("ascii")
            canonical = self._codec.canonicalize_string(value)
            return _canonical_session(canonical)
        except Exception:
            raise ValueError(_SAFE_CONVERSION_ERROR) from None

    async def convert_telethon_file(self, data: bytes) -> bytes:
        try:
            if not data.startswith(b"SQLite format 3\x00"):
                raise ValueError
            with tempfile.TemporaryDirectory(prefix="telegram-session-") as directory:
                path = Path(directory) / "import.session"
                path.write_bytes(data)
                canonical = self._codec.sqlite_to_string(path)
            return _canonical_session(canonical)
        except Exception:
            raise ValueError(_SAFE_CONVERSION_ERROR) from None


class DefaultDenyTDataConverter:
    """Secure default until an explicitly reviewed TData conversion backend is installed."""

    async def convert_tdata(self, data: bytes) -> bytes:
        raise ValueError(_SAFE_CONVERSION_ERROR)


class TelethonAuthorizationClientFactory:
    """Create fresh in-memory Telethon clients for phone and QR state machines."""

    def __init__(self, api_id: int, api_hash: str) -> None:
        self._api_id = api_id
        self._api_hash = api_hash

    def phone(self) -> "TelethonPhoneAuthorizationClient":
        return TelethonPhoneAuthorizationClient(self._new_client())

    def qr(self) -> "TelethonQrAuthorizationClient":
        return TelethonQrAuthorizationClient(self._new_client())

    def _new_client(self):
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            return TelegramClient(
                StringSession(),
                self._api_id,
                self._api_hash,
                receive_updates=False,
                auto_reconnect=False,
            )
        except Exception:
            raise RuntimeError("telegram client unavailable") from None


class TelethonPhoneAuthorizationClient:
    def __init__(self, client: object) -> None:
        self._client = client

    async def request_code(self, phone: str) -> object:
        await self._client.connect()
        return await self._client.send_code_request(phone)

    async def sign_in(self, phone: str, code: str) -> tuple[int, str | None]:
        identity = await self._client.sign_in(phone=phone, code=code)
        return _identity(identity)

    async def check_password(self, password: str) -> tuple[int, str | None]:
        identity = await self._client.sign_in(password=password)
        return _identity(identity)

    async def export_session(self) -> bytes:
        try:
            from telethon.sessions import StringSession

            return _canonical_session(StringSession.save(self._client.session))
        except Exception:
            raise RuntimeError("telegram authorization unavailable") from None


class TelethonQrAuthorizationClient:
    def __init__(self, client: object) -> None:
        self._client = client

    async def request_qr(self) -> object:
        await self._client.connect()
        return await self._client.qr_login()

    async def complete_qr(self, token: object) -> tuple[int, str | None]:
        wait = getattr(token, "wait", None)
        if not callable(wait):
            raise RuntimeError("telegram QR authorization unavailable")
        return _identity(await wait())


class TelethonRuntimeClientFactory:
    """Create a concrete client from an account-bound encrypted session reference."""

    def __init__(self, session_store: EncryptedSessionStore, *, api_id: int, api_hash: str) -> None:
        self._session_store = session_store
        self._api_id = api_id
        self._api_hash = api_hash

    async def create(self, session: SessionRef, proxy: ProxyConfig) -> "TelethonClientAdapter":
        payload = await asyncio.to_thread(self._session_store.get, session)
        try:
            if not payload.startswith(_SESSION_PREFIX):
                raise ValueError
            value = payload[len(_SESSION_PREFIX) :].decode("ascii")
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            client = TelegramClient(
                StringSession(value),
                self._api_id,
                self._api_hash,
                proxy=_proxy_details(proxy),
                auto_reconnect=False,
                receive_updates=True,
            )
            return TelethonClientAdapter(client)
        except Exception:
            raise RuntimeError("telegram client unavailable") from None


class TelethonClientAdapter:
    """Concrete lifecycle and gateway adapter around one Telethon client."""

    def __init__(self, client: object) -> None:
        self._client = client

    async def connect(self) -> None:
        await self._client.connect()

    async def is_authorized(self) -> bool:
        return bool(await self._client.is_user_authorized())

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def send_message(self, peer_id: int, message_text: str, idempotency_key: str) -> str:
        message = await self._client.send_message(peer_id, message_text)
        identifier = getattr(message, "id", None)
        if type(identifier) is not int or identifier <= 0:
            raise RuntimeError("telegram send outcome unavailable")
        return str(identifier)

    async def reconcile_message(self, peer_id: int, idempotency_key: str) -> str | None:
        # Telethon's high-level send has no durable caller idempotency key. The
        # gateway therefore remains default-deny after any ambiguous outcome.
        return None


class TelegramBotApiClient:
    """Minimal concrete Bot API identity probe with bounded, redacted failures."""

    def __init__(
        self,
        *,
        opener: Callable[..., object] = urlopen,
        timeout_seconds: float = 10.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("invalid Bot API timeout")
        self._opener = opener
        self._timeout_seconds = timeout_seconds

    async def get_me(self, token: str) -> tuple[int, str | None]:
        return await asyncio.to_thread(self._get_me, token)

    def _get_me(self, token: str) -> tuple[int, str | None]:
        try:
            request = Request(
                f"https://api.telegram.org/bot{token}/getMe",
                headers={"Accept": "application/json", "User-Agent": "ai-sales-stage0/1"},
            )
            response = self._opener(request, timeout=self._timeout_seconds)
            with response:
                payload = response.read(64 * 1024 + 1)
            if len(payload) > 64 * 1024:
                raise ValueError
            document = json.loads(payload.decode("utf-8"))
            result = document["result"]
            if document.get("ok") is not True or type(result.get("id")) is not int:
                raise ValueError
            username = result.get("username")
            if username is not None and not isinstance(username, str):
                raise ValueError
            return result["id"], username
        except Exception:
            raise RuntimeError("bot authorization unavailable") from None


def _canonical_session(value: object) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or not value.isascii():
        raise ValueError(_SAFE_CONVERSION_ERROR)
    return _SESSION_PREFIX + value.encode("ascii")


def _identity(identity: object) -> tuple[int, str | None]:
    identifier = getattr(identity, "id", None)
    username = getattr(identity, "username", None)
    if type(identifier) is not int or identifier <= 0 or (
        username is not None and not isinstance(username, str)
    ):
        raise RuntimeError("telegram identity unavailable")
    return identifier, username


def _proxy_details(proxy: ProxyConfig) -> dict[str, object]:
    split = urlsplit(proxy.endpoint)
    if split.scheme not in {"socks5", "http"} or split.hostname is None or split.port is None:
        raise RuntimeError("telegram proxy transport unsupported")
    details: dict[str, object] = {
        "proxy_type": split.scheme,
        "addr": split.hostname,
        "port": split.port,
        "rdns": True,
    }
    if proxy.username is not None:
        details["username"] = proxy.username.get_secret_value()
    if proxy.password is not None:
        details["password"] = proxy.password.get_secret_value()
    return details
