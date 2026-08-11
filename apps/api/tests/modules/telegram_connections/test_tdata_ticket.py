from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.modules.telegram_connections.tdata_ticket import TdataTicketRegistry, TicketRejected


OWNER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OWNER_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def test_ticket_is_owner_bound_one_time_and_expires() -> None:
    """A tdata upload must never be replayable or transferable between owners."""

    async def scenario() -> None:
        clock = [datetime(2026, 8, 11, tzinfo=UTC)]
        registry = TdataTicketRegistry(now=lambda: clock[0], ttl=timedelta(minutes=1))
        ticket = await registry.issue(OWNER_A)

        with pytest.raises(TicketRejected, match="^tdata ticket rejected$"):
            await registry.consume(OWNER_B, ticket.ticket_id)
        await registry.consume(OWNER_A, ticket.ticket_id)
        with pytest.raises(TicketRejected, match="^tdata ticket rejected$"):
            await registry.consume(OWNER_A, ticket.ticket_id)

        expired = await registry.issue(OWNER_A)
        clock[0] += timedelta(minutes=2)
        with pytest.raises(TicketRejected, match="^tdata ticket rejected$"):
            await registry.consume(OWNER_A, expired.ticket_id)

    asyncio.run(scenario())
