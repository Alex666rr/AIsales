"""Safe, persisted connection-state and injected runtime protocols."""

from collections.abc import Iterable
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    @field_validator("last_seen_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)


class ConnectionRecord(BaseModel):
    """The full non-secret persisted state required to resume a connection."""

    model_config = ConfigDict(frozen=True)

    account_id: UUID
    session_ref: SessionRef
    health: ConnectionHealth
    retry_count: int = Field(default=0, ge=0)
    retry_at: datetime | None = None
    version: int = Field(default=0, ge=0)
    lease_owner_id: UUID | None = None
    lease_expires_at: datetime | None = None
    fence_token: int = Field(default=0, ge=0)

    @field_validator("retry_at")
    @classmethod
    def _utc_retry_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("lease_expires_at")
    @classmethod
    def _utc_lease_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)


class ConnectionRepository(Protocol):
    """Authoritative PostgreSQL-compatible connection-state boundary."""

    async def get(self, account_id: UUID) -> ConnectionRecord | None:
        """Return the latest persisted record, not an in-memory cache."""

    async def save(self, record: ConnectionRecord) -> None:
        """Persist the next state before any retry is slept."""

    async def try_claim(self, account_id: UUID, owner_id: UUID, now: datetime, *, lease_seconds: float) -> ConnectionRecord | None:
        """Atomically claim an unleased non-terminal record for one supervisor."""

    async def save_claimed(self, record: ConnectionRecord, owner_id: UUID, *, release_lease: bool) -> ConnectionRecord | None:
        """CAS-save a claimed record; return ``None`` if it lost its claim/version race."""

    async def force_terminal(self, account_id: UUID, state: Literal["paused", "archived"], now: datetime) -> ConnectionRecord:
        """Atomically make pause/archive win and invalidate every outstanding claim."""

    async def renew_lease(self, record: ConnectionRecord, owner_id: UUID, now: datetime, *, lease_seconds: float) -> ConnectionRecord | None:
        """Advance version while retaining the owner and fencing token for a live client."""


class InMemoryConnectionRepository:
    """A test fake; production must provide a durable PostgreSQL implementation."""

    def __init__(self, records: Iterable[ConnectionRecord] = ()) -> None:
        self._records = {record.account_id: record for record in records}
        self.history: list[ConnectionRecord] = []
        self._lock = asyncio.Lock()

    async def get(self, account_id: UUID) -> ConnectionRecord | None:
        return self._records.get(account_id)

    async def save(self, record: ConnectionRecord) -> None:
        async with self._lock:
            self._records[record.account_id] = record
            self.history.append(record)

    async def try_claim(self, account_id: UUID, owner_id: UUID, now: datetime, *, lease_seconds: float) -> ConnectionRecord | None:
        async with self._lock:
            record = self._records.get(account_id)
            if record is None:
                return None
            if record.health.state in {"paused", "reauth_required", "blocked", "archived"}:
                return record
            if record.lease_owner_id is not None and (record.lease_expires_at is None or record.lease_expires_at > now):
                return None
            claimed = record.model_copy(update={"version": record.version + 1, "lease_owner_id": owner_id, "lease_expires_at": now + timedelta(seconds=lease_seconds), "fence_token": record.fence_token + 1})
            self._records[account_id] = claimed
            return claimed

    async def save_claimed(self, record: ConnectionRecord, owner_id: UUID, *, release_lease: bool) -> ConnectionRecord | None:
        async with self._lock:
            current = self._records.get(record.account_id)
            if (
                current is None
                or current.version != record.version
                or current.lease_owner_id != owner_id
                or current.health.state == "archived"
                or current.fence_token != record.fence_token
            ):
                return None
            saved = record.model_copy(
                update={"version": record.version + 1, "lease_owner_id": None if release_lease else owner_id, "lease_expires_at": None if release_lease else record.lease_expires_at}
            )
            self._records[record.account_id] = saved
            self.history.append(saved)
            return saved

    async def force_terminal(self, account_id: UUID, state: Literal["paused", "archived"], now: datetime) -> ConnectionRecord:
        async with self._lock:
            record = self._records.get(account_id)
            if record is None:
                raise KeyError("connection record was not found")
            if record.health.state == "archived":
                return record
            health = record.health.model_copy(update={"state": state, "last_seen_at": now, "error_code": None})
            saved = record.model_copy(
                update={"health": health, "retry_at": None, "version": record.version + 1, "lease_owner_id": None, "lease_expires_at": None, "fence_token": record.fence_token + 1}
            )
            self._records[account_id] = saved
            self.history.append(saved)
            return saved

    async def renew_lease(self, record: ConnectionRecord, owner_id: UUID, now: datetime, *, lease_seconds: float) -> ConnectionRecord | None:
        async with self._lock:
            current = self._records.get(record.account_id)
            if (
                current is None or current.version != record.version or current.lease_owner_id != owner_id
                or current.fence_token != record.fence_token or current.lease_expires_at is None
                or current.lease_expires_at <= now or current.health.state != "active"
            ):
                return None
            saved = record.model_copy(update={"version": record.version + 1, "lease_expires_at": now + timedelta(seconds=lease_seconds)})
            self._records[record.account_id] = saved
            return saved


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
