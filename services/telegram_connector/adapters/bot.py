"""Injected Bot API authorization adapter, kept distinct from MTProto sessions."""

import re
from typing import Protocol

from .base import SessionMaterial, SessionProbeResult


class BotApiClient(Protocol):
    async def get_me(self, token: str) -> tuple[int, str | None]: ...


_TOKEN = re.compile(r"^\d{1,20}:[A-Za-z0-9_-]{1,128}$")


class BotAdapter:
    """Validates a bot token through the injected Bot API without retaining the token."""

    name = "bot"
    session_kind = "bot_api"

    def __init__(self, client: BotApiClient) -> None:
        self._client = client

    async def probe(self, material: SessionMaterial) -> SessionProbeResult:
        identity = await self._identity(material)
        if identity is None:
            return SessionProbeResult(
                adapter=self.name,
                state="invalid",
                telegram_user_id=None,
                username=None,
                phone_masked=None,
                capabilities=frozenset(),
                error_code="invalid_bot_token",
            )
        user_id, username = identity
        return SessionProbeResult(
            adapter=self.name,
            state="authorized",
            telegram_user_id=user_id,
            username=username,
            phone_masked=None,
            capabilities=frozenset({"bot_api"}),
            error_code=None,
        )

    async def convert(self, material: SessionMaterial) -> bytes:
        identity = await self._identity(material)
        if identity is None:
            raise ValueError("bot authorization failed")
        # This marker is intentionally incompatible with user-account MTProto session bytes.
        return b"BOT_API_SESSION\x00\x01" + str(identity[0]).encode("ascii")

    async def _identity(self, material: SessionMaterial) -> tuple[int, str | None] | None:
        if material.adapter != self.name or material.payload:
            return None
        token = self._token(material)
        if token is None or _TOKEN.fullmatch(token) is None:
            return None
        try:
            identity = await self._client.get_me(token)
        except Exception:
            return None
        if not isinstance(identity, tuple) or len(identity) != 2 or type(identity[0]) is not int or identity[0] <= 0:
            return None
        username = identity[1]
        if username is not None and (not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username)):
            return None
        return identity

    @staticmethod
    def _token(material: SessionMaterial) -> str | None:
        for key, value in material.credentials:
            if key == "token":
                return value.get_secret_value()
        return None
