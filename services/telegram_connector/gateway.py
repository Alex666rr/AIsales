"""A synthetic-safe Telegram message gateway with durable idempotency semantics."""

import asyncio
from datetime import UTC, datetime
from typing import Callable, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from telegram_connector.compatibility import CompatibilityOutcome, CompatibilityRegistry
from telegram_connector.error_codes import TelegramErrorCode, TelegramGatewayError, map_telegram_error


class MessageCommand(BaseModel):
    """The sole outbound command shape; its raw text cannot serialize or appear in reprs."""

    model_config = ConfigDict(frozen=True)

    account_id: UUID
    peer_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    message_text: str = Field(min_length=1, max_length=4096, exclude=True, repr=False)


class DeliveryResult(BaseModel):
    """Content-free result suitable for durable idempotency persistence."""

    model_config = ConfigDict(frozen=True)

    external_message_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["sent", "reconciled"]


class IncomingTelegramEvent(BaseModel):
    """Ephemeral adapter input. Raw Telegram text is excluded from every public representation."""

    model_config = ConfigDict(frozen=True)

    update_id: int = Field(ge=0)
    peer_kind: str
    sender_kind: str
    sender_id: int | None
    peer_id: int | None
    message_text: str = Field(exclude=True, repr=False)
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)


class TelegramUpdate(BaseModel):
    """Default-deny normalized incoming metadata; it deliberately contains no message body."""

    model_config = ConfigDict(frozen=True)

    update_id: int = Field(ge=0)
    sender_id: int = Field(gt=0)
    peer_id: int = Field(gt=0)
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)


class OutboundTelegramClient(Protocol):
    """The injected boundary that alone may produce a Telegram network effect."""

    async def send_message(self, peer_id: int, message_text: str, idempotency_key: str) -> str:
        """Send exactly one supplied message through the active client."""

    async def reconcile_message(self, peer_id: int, idempotency_key: str) -> str | None:
        """Find a previous remote send without exposing message text."""


DeliveryState = Literal["pending", "uncertain", "completed"]
ReservationAction = Literal["send", "wait", "reconcile", "completed"]


class DeliveryRecord(BaseModel):
    """Durable, content-free idempotency state; a PostgreSQL implementation is authoritative."""

    model_config = ConfigDict(frozen=True)

    account_id: UUID
    peer_id: int = Field(gt=0)
    idempotency_key: str
    state: DeliveryState
    result: DeliveryResult | None = None
    error_code: TelegramErrorCode | None = None


class DeliveryReservation(BaseModel):
    """The atomic outcome of a repository reservation transaction."""

    model_config = ConfigDict(frozen=True)

    action: ReservationAction
    result: DeliveryResult | None = None


class MessageDeliveryRepository(Protocol):
    """PostgreSQL source-of-truth boundary for atomic, durable idempotency state."""

    async def reserve(self, command: MessageCommand) -> DeliveryReservation:
        """Atomically persist a pending record before one caller may send."""

    async def wait(self, command: MessageCommand) -> None:
        """Wait for the winning sender to complete or declare an uncertain outcome."""

    async def complete(self, command: MessageCommand, result: DeliveryResult) -> None:
        """Atomically persist the safe terminal result and release duplicate waiters."""

    async def mark_uncertain(self, command: MessageCommand, error_code: TelegramErrorCode) -> None:
        """Persist ambiguity before a caller attempts remote reconciliation."""

    async def allow_resend_after_reconcile_miss(self, command: MessageCommand) -> DeliveryReservation:
        """Atomically make one reconciled-miss caller the next sender."""


class InMemoryMessageDeliveryRepository:
    """Transactional test fake; production supplies the corresponding PostgreSQL repository."""

    def __init__(self, records: tuple[DeliveryRecord, ...] = ()) -> None:
        # Rows loaded by a fresh repository instance have no live in-process
        # sender.  Conservatively recover a persisted pending attempt as
        # uncertain so restart reconciles before granting another send.
        self._records = {
            (record.account_id, record.idempotency_key): (
                record.model_copy(update={"state": "uncertain"}) if record.state == "pending" else record
            )
            for record in records
        }
        self._events = {key: asyncio.Event() for key in self._records}
        for key, record in self._records.items():
            if record.state != "pending":
                self._events[key].set()
        self._lock = asyncio.Lock()

    async def reserve(self, command: MessageCommand) -> DeliveryReservation:
        key = self._key(command)
        async with self._lock:
            record = self._records.get(key)
            if record is None:
                self._records[key] = DeliveryRecord(
                    account_id=command.account_id,
                    peer_id=command.peer_id,
                    idempotency_key=command.idempotency_key,
                    state="pending",
                )
                self._events[key] = asyncio.Event()
                return DeliveryReservation(action="send")
            if record.peer_id != command.peer_id:
                return DeliveryReservation(action="completed", result=None)
            if record.state == "completed":
                return DeliveryReservation(action="completed", result=record.result)
            if record.state == "uncertain":
                return DeliveryReservation(action="reconcile")
            return DeliveryReservation(action="wait")

    async def wait(self, command: MessageCommand) -> None:
        await self._events[self._key(command)].wait()

    async def complete(self, command: MessageCommand, result: DeliveryResult) -> None:
        key = self._key(command)
        async with self._lock:
            current = self._records.get(key)
            if current is None:
                raise KeyError("idempotency reservation was not found")
            self._records[key] = current.model_copy(update={"state": "completed", "result": result, "error_code": None})
            self._events[key].set()

    async def mark_uncertain(self, command: MessageCommand, error_code: TelegramErrorCode) -> None:
        key = self._key(command)
        async with self._lock:
            current = self._records.get(key)
            if current is None:
                raise KeyError("idempotency reservation was not found")
            self._records[key] = current.model_copy(update={"state": "uncertain", "error_code": error_code})
            self._events[key].set()

    async def allow_resend_after_reconcile_miss(self, command: MessageCommand) -> DeliveryReservation:
        key = self._key(command)
        async with self._lock:
            current = self._records.get(key)
            if current is None:
                raise KeyError("idempotency reservation was not found")
            if current.state == "completed":
                return DeliveryReservation(action="completed", result=current.result)
            if current.state == "uncertain":
                self._records[key] = current.model_copy(update={"state": "pending"})
                self._events[key] = asyncio.Event()
                return DeliveryReservation(action="send")
            return DeliveryReservation(action="wait")

    async def record(self, command: MessageCommand) -> DeliveryRecord | None:
        """Test-only safe inspection; it returns no raw message text."""
        return self._records.get(self._key(command))

    @staticmethod
    def _key(command: MessageCommand) -> tuple[UUID, str]:
        return command.account_id, command.idempotency_key


class TelegramGateway:
    """Send with atomic reservation and reconciliation, never with an un-injected client."""

    def __init__(
        self,
        *,
        client: OutboundTelegramClient,
        repository: MessageDeliveryRepository,
        compatibility: CompatibilityRegistry,
        adapter: str,
        adapter_version: str,
        proxy_id: UUID | None,
        connection_is_active: Callable[[], bool],
    ) -> None:
        self._client = client
        self._repository = repository
        self._compatibility = compatibility
        self._adapter = adapter
        self._adapter_version = adapter_version
        self._proxy_id = proxy_id
        self._connection_is_active = connection_is_active

    async def send(self, command: MessageCommand) -> DeliveryResult:
        """Return one durable result; ambiguous sends always reconcile before a later resend."""
        if not self._connection_is_active():
            self._record("connection_inactive")
            raise TelegramGatewayError("connection_inactive")
        while True:
            reservation = await self._repository.reserve(command)
            if reservation.action == "completed":
                if reservation.result is None:
                    raise TelegramGatewayError("invalid_peer")
                return reservation.result
            if reservation.action == "wait":
                await self._repository.wait(command)
                continue
            if reservation.action == "reconcile":
                reconciled = await self._reconcile(command)
                if reconciled is not None:
                    return reconciled
                reservation = await self._repository.allow_resend_after_reconcile_miss(command)
                if reservation.action == "completed" and reservation.result is not None:
                    return reservation.result
                if reservation.action == "wait":
                    await self._repository.wait(command)
                    continue
            return await self._send_reserved(command)

    def normalize_incoming(self, event: IncomingTelegramEvent) -> TelegramUpdate | None:
        """Default-deny everything except a well-formed private non-service user message."""
        if (
            event.peer_kind != "private"
            or event.sender_kind != "user"
            or event.sender_id is None
            or event.peer_id is None
            or event.sender_id <= 0
            or event.peer_id <= 0
        ):
            return None
        return TelegramUpdate(
            update_id=event.update_id,
            sender_id=event.sender_id,
            peer_id=event.peer_id,
            received_at=event.received_at,
        )

    async def _send_reserved(self, command: MessageCommand) -> DeliveryResult:
        try:
            external_message_id = await self._client.send_message(command.peer_id, command.message_text, command.idempotency_key)
        except Exception as error:
            code = map_telegram_error(error)
            await self._repository.mark_uncertain(command, code)
            reconciled = await self._reconcile(command)
            if reconciled is not None:
                return reconciled
            self._record(code)
            raise TelegramGatewayError(code) from None
        result = DeliveryResult(external_message_id=str(external_message_id), outcome="sent")
        await self._repository.complete(command, result)
        self._record("sent")
        return result

    async def _reconcile(self, command: MessageCommand) -> DeliveryResult | None:
        try:
            external_message_id = await self._client.reconcile_message(command.peer_id, command.idempotency_key)
        except Exception as error:
            code = map_telegram_error(error)
            self._record(code)
            raise TelegramGatewayError(code) from None
        if external_message_id is None:
            return None
        result = DeliveryResult(external_message_id=str(external_message_id), outcome="reconciled")
        await self._repository.complete(command, result)
        self._record("reconciled")
        return result

    def _record(self, outcome: CompatibilityOutcome) -> None:
        self._compatibility.record(
            adapter=self._adapter,
            adapter_version=self._adapter_version,
            proxy=self._proxy_id,
            outcome=outcome,
        )
