"""SQLAlchemy/PostgreSQL source-of-truth storage for Telegram gateway state."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Integer, MetaData, String, Table, Column, and_, or_, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from telegram_connector.compatibility import CompatibilityOutcome, CompatibilityRecord
from telegram_connector.error_codes import TelegramErrorCode
from telegram_connector.gateway import DeliveryRecord, DeliveryReservation, DeliveryResult, MessageCommand
from telegram_connector.proxies import ProxyConfig


gateway_metadata = MetaData()


class RepositoryClock(Protocol):
    def now(self) -> datetime: ...

message_deliveries = Table(
    "telegram_message_deliveries",
    gateway_metadata,
    Column("account_id", String(36), primary_key=True),
    Column("idempotency_key", String(128), primary_key=True),
    Column("peer_id", BigInteger, nullable=False),
    Column("state", String(16), nullable=False),
    Column("external_message_id", String(128)),
    Column("outcome", String(16)),
    Column("error_code", String(64)),
    Column("owner_id", String(36)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("fence_token", Integer, nullable=False, default=0),
)

compatibility_rows = Table(
    "telegram_compatibility_rows",
    gateway_metadata,
    Column("adapter", String(64), primary_key=True),
    Column("adapter_version", String(64), primary_key=True),
    # PostgreSQL UNIQUE treats NULL values as distinct, so a non-null empty key
    # represents direct/no-proxy operation and preserves one-row semantics.
    Column("proxy_key", String(36), primary_key=True),
    Column("outcome", String(64), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)


def create_gateway_schema(bind: Engine) -> None:
    """Create the same tables supplied by the PostgreSQL Alembic migration for local contract tests."""
    gateway_metadata.create_all(bind)


class SqlAlchemyMessageDeliveryRepository:
    """Durable SQLAlchemy repository; PostgreSQL transactions and row locks are authoritative."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        clock: RepositoryClock | Callable[[], datetime] | None = None,
        lease_seconds: float = 30.0,
        wait_seconds: float = 0.01,
        owner_id: UUID | None = None,
    ) -> None:
        if lease_seconds <= 0 or wait_seconds <= 0:
            raise ValueError("invalid durable gateway timing")
        self._sessions = sessions
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_seconds = lease_seconds
        self._wait_seconds = wait_seconds
        self._owner_id = owner_id or uuid4()

    @property
    def lease_seconds(self) -> float:
        return self._lease_seconds

    async def reserve(self, command: MessageCommand) -> DeliveryReservation:
        """Atomically create or lock one row; expired pending rows become uncertain, never sendable."""
        for _ in range(2):
            try:
                return self._reserve_once(command)
            except IntegrityError:
                # A concurrent PostgreSQL insert won the unique-key race; lock
                # the newly persisted row on the next transaction.
                continue
        raise RuntimeError("durable idempotency reservation failed")

    def _reserve_once(self, command: MessageCommand) -> DeliveryReservation:
        now = self._now()
        with self._sessions.begin() as session:
            row = self._row_for_update(session, command)
            if row is None:
                session.execute(insert(message_deliveries).values(
                    account_id=str(command.account_id), idempotency_key=command.idempotency_key,
                    peer_id=command.peer_id, state="pending", owner_id=str(self._owner_id),
                    lease_expires_at=now + timedelta(seconds=self._lease_seconds), fence_token=1,
                ))
                return DeliveryReservation(action="send", fence_token=1)
            if int(row["peer_id"]) != command.peer_id:
                return DeliveryReservation(action="completed", result=None, fence_token=int(row["fence_token"]))
            if row["state"] == "completed":
                return DeliveryReservation(action="completed", result=self._result(row), fence_token=int(row["fence_token"]))
            if row["state"] == "pending":
                expiry = self._utc(row["lease_expires_at"])
                if expiry is None or expiry <= now:
                    session.execute(update(message_deliveries).where(
                        message_deliveries.c.account_id == str(command.account_id),
                        message_deliveries.c.idempotency_key == command.idempotency_key,
                        message_deliveries.c.state == "pending",
                        message_deliveries.c.fence_token == int(row["fence_token"]),
                    ).values(state="uncertain", owner_id=None, lease_expires_at=None))
                    return DeliveryReservation(action="reconcile", fence_token=int(row["fence_token"]))
                return DeliveryReservation(action="wait", fence_token=int(row["fence_token"]))
            return DeliveryReservation(action="reconcile", fence_token=int(row["fence_token"]))

    async def wait(self, command: MessageCommand) -> None:
        """Bounded polling lets an expired owner be recovered by a later reserve call."""
        await asyncio.sleep(self._wait_seconds)

    async def complete(self, command: MessageCommand, reservation: DeliveryReservation, result: DeliveryResult | None) -> bool:
        if result is None:
            return False
        with self._sessions.begin() as session:
            changed = session.execute(update(message_deliveries).where(
                message_deliveries.c.account_id == str(command.account_id),
                message_deliveries.c.idempotency_key == command.idempotency_key,
                or_(
                    message_deliveries.c.state == "uncertain",
                    and_(message_deliveries.c.state == "pending", message_deliveries.c.owner_id == str(self._owner_id),
                         message_deliveries.c.fence_token == reservation.fence_token),
                ),
            ).values(
                state="completed", external_message_id=result.external_message_id, outcome=result.outcome,
                error_code=None, owner_id=None, lease_expires_at=None,
            )).rowcount
        return changed == 1

    async def mark_uncertain(self, command: MessageCommand, reservation: DeliveryReservation, error_code: TelegramErrorCode) -> bool:
        # Intentionally permits marking an expired *own* fence as uncertain: an
        # ambiguous old network effect must never remain a candidate for resend.
        with self._sessions.begin() as session:
            changed = session.execute(update(message_deliveries).where(
                message_deliveries.c.account_id == str(command.account_id),
                message_deliveries.c.idempotency_key == command.idempotency_key,
                message_deliveries.c.state == "pending",
                message_deliveries.c.owner_id == str(self._owner_id),
                message_deliveries.c.fence_token == reservation.fence_token,
            ).values(state="uncertain", error_code=error_code, owner_id=None, lease_expires_at=None)).rowcount
        return changed == 1

    async def renew(self, command: MessageCommand, reservation: DeliveryReservation) -> bool:
        with self._sessions.begin() as session:
            changed = session.execute(update(message_deliveries).where(
                message_deliveries.c.account_id == str(command.account_id),
                message_deliveries.c.idempotency_key == command.idempotency_key,
                message_deliveries.c.state == "pending",
                message_deliveries.c.owner_id == str(self._owner_id),
                message_deliveries.c.fence_token == reservation.fence_token,
            ).values(lease_expires_at=self._now() + timedelta(seconds=self._lease_seconds))).rowcount
        return changed == 1

    async def allow_resend_after_reconcile_miss(self, command: MessageCommand, reservation: DeliveryReservation) -> DeliveryReservation:
        now = self._now()
        with self._sessions.begin() as session:
            row = self._row_for_update(session, command)
            if row is None:
                raise KeyError("idempotency reservation was not found")
            if row["state"] == "completed":
                return DeliveryReservation(action="completed", result=self._result(row), fence_token=int(row["fence_token"]))
            if row["state"] == "uncertain":
                fence = int(row["fence_token"]) + 1
                session.execute(update(message_deliveries).where(
                    message_deliveries.c.account_id == str(command.account_id),
                    message_deliveries.c.idempotency_key == command.idempotency_key,
                    message_deliveries.c.state == "uncertain",
                    message_deliveries.c.fence_token == int(row["fence_token"]),
                ).values(
                    state="pending", owner_id=str(self._owner_id),
                    lease_expires_at=now + timedelta(seconds=self._lease_seconds), fence_token=fence,
                ))
                return DeliveryReservation(action="send", fence_token=fence)
            return DeliveryReservation(action="wait", fence_token=int(row["fence_token"]))

    def _row_for_update(self, session: Session, command: MessageCommand):
        return session.execute(select(message_deliveries).where(
            message_deliveries.c.account_id == str(command.account_id),
            message_deliveries.c.idempotency_key == command.idempotency_key,
        ).with_for_update()).mappings().one_or_none()

    @staticmethod
    def _result(row) -> DeliveryResult | None:
        if row["external_message_id"] is None or row["outcome"] not in {"sent", "reconciled"}:
            return None
        return DeliveryResult(external_message_id=str(row["external_message_id"]), outcome=row["outcome"])

    def _now(self) -> datetime:
        source = self._clock
        value = source.now() if hasattr(source, "now") else source()
        return self._utc(value) or datetime.now(UTC)

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlAlchemyCompatibilityRegistry:
    """Durable one-row-per-adapter/version/proxy compatibility evidence registry."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record(
        self, *, adapter: str, adapter_version: str, proxy: ProxyConfig | UUID | None = None,
        outcome: CompatibilityOutcome, recorded_at: datetime | None = None,
    ) -> CompatibilityRecord:
        proxy_id = proxy.proxy_id if isinstance(proxy, ProxyConfig) else proxy
        row = CompatibilityRecord(adapter=adapter, adapter_version=adapter_version, proxy_id=proxy_id,
                                  outcome=outcome, recorded_at=recorded_at or datetime.now(UTC))
        values = {"adapter": row.adapter, "adapter_version": row.adapter_version, "proxy_key": str(row.proxy_id or ""),
                  "outcome": row.outcome, "recorded_at": row.recorded_at}
        with self._sessions.begin() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dialect_insert
                statement = dialect_insert(compatibility_rows).values(**values).on_conflict_do_update(
                    index_elements=["adapter", "adapter_version", "proxy_key"],
                    set_={"outcome": row.outcome, "recorded_at": row.recorded_at},
                )
            else:
                from sqlalchemy.dialects.sqlite import insert as dialect_insert
                statement = dialect_insert(compatibility_rows).values(**values).on_conflict_do_update(
                    index_elements=["adapter", "adapter_version", "proxy_key"],
                    set_={"outcome": row.outcome, "recorded_at": row.recorded_at},
                )
            session.execute(statement)
        return row

    def records(self) -> tuple[CompatibilityRecord, ...]:
        with self._sessions() as session:
            rows = session.execute(select(compatibility_rows).order_by(
                compatibility_rows.c.adapter, compatibility_rows.c.adapter_version, compatibility_rows.c.proxy_key
            )).mappings().all()
        return tuple(CompatibilityRecord(
            adapter=row["adapter"], adapter_version=row["adapter_version"],
            proxy_id=UUID(row["proxy_key"]) if row["proxy_key"] else None,
            outcome=row["outcome"], recorded_at=SqlAlchemyMessageDeliveryRepository._utc(row["recorded_at"]) or datetime.now(UTC),
        ) for row in rows)
