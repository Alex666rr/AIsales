"""Safe, persisted connection-state and injected runtime protocols."""

from collections.abc import Iterable
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from telegram_connector.proxies import ProxyConfig
from telegram_connector.session_store import SessionRef


class ConnectionHealth(BaseModel):
    """The public, immutable health projection for one account."""

    model_config = ConfigDict(frozen=True)

    state: Literal["quarantine", "active", "paused", "reauth_required", "limited", "blocked", "archived"]
    last_seen_at: datetime | None
    proxy_ip: str | None
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None


class ConnectionRecord(BaseModel):
    """The full non-secret persisted state required to resume a connection."""

    model_config = ConfigDict(frozen=True)

    account_id: UUID
    session_ref: SessionRef
    health: ConnectionHealth
    retry_count: int = Field(default=0, ge=0)
    retry_at: datetime | None = None


class ConnectionRepository(Protocol):
    """Authoritative PostgreSQL-compatible connection-state boundary."""

    async def get(self, account_id: UUID) -> ConnectionRecord | None:
        """Return the latest persisted record, not an in-memory cache."""

    async def save(self, record: ConnectionRecord) -> None:
        """Persist the next state before any retry is slept."""


class InMemoryConnectionRepository:
    """A test fake; production must provide a durable PostgreSQL implementation."""

    def __init__(self, records: Iterable[ConnectionRecord] = ()) -> None:
        self._records = {record.account_id: record for record in records}
        self.history: list[ConnectionRecord] = []

    async def get(self, account_id: UUID) -> ConnectionRecord | None:
        return self._records.get(account_id)

    async def save(self, record: ConnectionRecord) -> None:
        self._records[record.account_id] = record
        self.history.append(record)


class TelegramClient(Protocol):
    """Minimal async client contract; no session material crosses this boundary."""

    async def connect(self) -> None:
        """Open the configured client connection."""

    async def is_authorized(self) -> bool:
        """Report whether the encrypted session is still authorized."""

    async def disconnect(self) -> None:
        """Gracefully close the client, including after cancellation."""


class TelegramClientFactory(Protocol):
    """Creates a client using a reference, never plaintext session bytes."""

    async def create(self, session: SessionRef, proxy: ProxyConfig) -> TelegramClient:
        """Return an unconnected client for this persisted assignment."""


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware current timestamp."""


class Sleeper(Protocol):
    async def sleep(self, seconds: float) -> None:
        """Wait through injected time for deterministic tests."""


class AuthorizationLostError(Exception):
    """Safe signal that session authorization is no longer valid."""

    def __init__(self) -> None:
        super().__init__("authorization lost")


class AccountBlockedError(Exception):
    """Safe signal that the account must not be retried."""

    def __init__(self) -> None:
        super().__init__("account blocked")


class FloodWaitError(Exception):
    """Safe normalized retry-after signal; raw Telegram text never crosses the boundary."""

    def __init__(self, retry_after_seconds: int) -> None:
        if retry_after_seconds < 1:
            raise ValueError("invalid retry-after")
        self.retry_after_seconds = retry_after_seconds
        super().__init__("rate limit active")
