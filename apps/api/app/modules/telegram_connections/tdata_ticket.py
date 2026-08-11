"""One-time, owner-bound tickets for a local tdata conversion handoff."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4


class TicketRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("tdata ticket rejected")


@dataclass(frozen=True, slots=True)
class TdataTicket:
    ticket_id: UUID
    expires_at: datetime


@dataclass(slots=True)
class _TicketRecord:
    owner_id: UUID
    expires_at: datetime


class TdataTicketRegistry:
    """Bounded ephemeral registry; ticket state never contains raw tdata."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("invalid tdata ticket ttl")
        self._now = now
        self._ttl = ttl
        self._records: dict[UUID, _TicketRecord] = {}
        self._lock = asyncio.Lock()

    async def issue(self, owner_id: UUID) -> TdataTicket:
        now = self._utc_now()
        async with self._lock:
            self._prune_locked(now)
            ticket_id = uuid4()
            expiry = now + self._ttl
            self._records[ticket_id] = _TicketRecord(owner_id=owner_id, expires_at=expiry)
            return TdataTicket(ticket_id=ticket_id, expires_at=expiry)

    async def consume(self, owner_id: UUID, ticket_id: UUID) -> None:
        now = self._utc_now()
        async with self._lock:
            self._prune_locked(now)
            record = self._records.get(ticket_id)
            if record is None or record.owner_id != owner_id or record.expires_at <= now:
                raise TicketRejected()
            self._records.pop(ticket_id, None)

    def _prune_locked(self, now: datetime) -> None:
        for ticket_id, record in tuple(self._records.items()):
            if record.expires_at <= now:
                self._records.pop(ticket_id, None)

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ticket clock must be timezone-aware")
        return value.astimezone(UTC)
