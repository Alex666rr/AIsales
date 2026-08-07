"""Shared immutable value types for the Telegram prototype."""

from datetime import UTC, datetime
from typing import TypeAlias
from uuid import UUID

AccountId: TypeAlias = UUID
OrganizationId: TypeAlias = UUID
ProxyId: TypeAlias = UUID
UtcTimestamp: TypeAlias = datetime


def serialize_utc_timestamp(value: UtcTimestamp) -> str:
    """Serialize an aware timestamp as a UTC ISO-8601 value."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("UtcTimestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def deserialize_utc_timestamp(value: str) -> UtcTimestamp:
    """Deserialize an ISO-8601 value into an aware UTC timestamp."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("UtcTimestamp must include a timezone offset")
    return parsed.astimezone(UTC)
