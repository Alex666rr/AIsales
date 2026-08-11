"""Safe, content-free adapter and proxy compatibility evidence."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from telegram_connector.proxies import ProxyConfig


CompatibilityOutcome = Literal[
    "sent",
    "reconciled",
    "connection_inactive",
    "invalid_peer",
    "privacy_restricted",
    "paid_message_required",
    "rate_limited",
    "authorization_lost",
    "account_blocked",
    "timeout",
    "telegram_unknown",
]


class CompatibilityRecord(BaseModel):
    """A safe compatibility row; message content and proxy endpoint never belong here."""

    model_config = ConfigDict(frozen=True)

    adapter: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    adapter_version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    proxy_id: UUID | None
    outcome: CompatibilityOutcome
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)


class CompatibilityRegistry:
    """Test-only in-memory registry; deployment persists the same safe row shape in PostgreSQL."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, UUID | None], CompatibilityRecord] = {}

    def record(
        self,
        *,
        adapter: str,
        adapter_version: str,
        proxy: ProxyConfig | UUID | None = None,
        outcome: CompatibilityOutcome,
        recorded_at: datetime | None = None,
    ) -> CompatibilityRecord:
        """Record only identity metadata and a normalized outcome."""
        proxy_id = proxy.proxy_id if isinstance(proxy, ProxyConfig) else proxy
        row = CompatibilityRecord(
            adapter=adapter,
            adapter_version=adapter_version,
            proxy_id=proxy_id,
            outcome=outcome,
            recorded_at=recorded_at or datetime.now(UTC),
        )
        self._records[(row.adapter, row.adapter_version, row.proxy_id)] = row
        return row

    def records(self) -> tuple[CompatibilityRecord, ...]:
        """Return immutable safe evidence rows."""
        return tuple(self._records.values())
