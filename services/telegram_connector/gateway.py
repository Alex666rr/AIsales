"""A synthetic-safe Telegram message gateway with durable idempotency semantics."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal, Protocol
from uuid import UUID, uuid4
from weakref import WeakSet

from pydantic import BaseModel, ConfigDict, Field, field_validator

from telegram_connector.compatibility import CompatibilityOutcome, CompatibilityRegistry
from telegram_connector.error_codes import TelegramErrorCode, TelegramGatewayError, map_telegram_error


class MessageCommand(BaseModel):
    """Content-free public command metadata; its body is an opaque vault handle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: UUID
    peer_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    body_handle: UUID

    @classmethod
    def create(cls, *, account_id: UUID, peer_id: int, idempotency_key: str, body_handle: UUID) -> "MessageCommand":
        """Create public metadata after a composition-bound vault has accepted the body."""
        return cls(account_id=account_id, peer_id=peer_id, idempotency_key=idempotency_key, body_handle=body_handle)

    def __reduce_ex__(self, protocol: int):
        raise TypeError("message command serialization is disabled")

    def __getstate__(self):
        raise TypeError("message command serialization is disabled")

    def model_copy(self, *, update=None, deep: bool = False):
        return super().model_copy(update=update, deep=deep)


class _EphemeralBodyVault:
    """Gateway-local body store; raw text never becomes command or durable state."""

    def __init__(self) -> None:
        self._bodies: dict[UUID, str] = {}

    def store(self, body: object) -> UUID:
        if not isinstance(body, str) or not 1 <= len(body) <= 4096:
            raise ValueError("invalid synthetic message body")
        handle = uuid4()
        self._bodies[handle] = body
        return handle

    def read(self, handle: UUID) -> str:
        try:
            return self._bodies[handle]
        except KeyError:
            raise TelegramGatewayError("telegram_unknown") from None

    def discard(self, handle: UUID) -> None:
        self._bodies.pop(handle, None)


class DeliveryResult(BaseModel):
    """Content-free result suitable for durable idempotency persistence."""

    model_config = ConfigDict(frozen=True)

    external_message_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["sent", "reconciled"]


class _InboundCapability:
    """Opaque instance-bound adapter proof; raw text and public labels never enter it."""
    __slots__ = ("_issuer", "update_id", "sender_id", "peer_id", "received_at")

    def __init__(self, issuer: object, update_id: int, sender_id: int, peer_id: int, received_at: datetime) -> None:
        self._issuer, self.update_id, self.sender_id, self.peer_id, self.received_at = issuer, update_id, sender_id, peer_id, received_at


class _InboundDecoder:
    __slots__ = ("_issuer", "_bound", "__weakref__")

    def __init__(self, issuer: object) -> None:
        self._issuer = issuer
        self._bound = False

    def _bind_gateway(self) -> object:
        if self._bound:
            raise ValueError("inbound decoder is already bound")
        self._bound = True
        return self._issuer

    def private_user(self, *, update_id: int, sender_id: int, peer_id: int, received_at: datetime) -> object:
        if update_id < 0 or sender_id <= 0 or peer_id <= 0 or received_at.tzinfo is None:
            raise ValueError("invalid authenticated inbound identity")
        return _InboundCapability(self._issuer, update_id, sender_id, peer_id, received_at.astimezone(UTC))


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


class ApprovedAdapterRegistry:
    """Composition-root allow-list that alone issues remote-deduplication authority."""

    def __init__(self, approved: frozenset[tuple[str, str]]) -> None:
        self._approved = approved
        self._issuer = object()

    def bind_remote_deduplication(self, *, client: OutboundTelegramClient, adapter: str, adapter_version: str) -> object:
        if (adapter, adapter_version) not in self._approved:
            raise ValueError("adapter/version lacks approved remote idempotency")
        capability = _RemoteDeduplicationCapability(self._issuer, client, adapter, adapter_version)
        _ISSUED_REMOTE_DEDUPLICATION_CAPABILITIES.add(capability)
        return capability

    def bind_inbound_decoder(self, *, adapter: str, adapter_version: str) -> object:
        """Issue the adapter-composition decoder for exactly one gateway classifier."""
        if (adapter, adapter_version) not in self._approved:
            raise ValueError("adapter/version lacks approved inbound provenance")
        decoder = _InboundDecoder(object())
        _ISSUED_INBOUND_DECODERS.add(decoder)
        return decoder


class _RemoteDeduplicationCapability:
    __slots__ = ("issuer", "client", "adapter", "adapter_version", "__weakref__")

    def __init__(self, issuer: object, client: OutboundTelegramClient, adapter: str, adapter_version: str) -> None:
        self.issuer, self.client, self.adapter, self.adapter_version = issuer, client, adapter, adapter_version


_ISSUED_REMOTE_DEDUPLICATION_CAPABILITIES: WeakSet[_RemoteDeduplicationCapability] = WeakSet()
_ISSUED_INBOUND_DECODERS: WeakSet[_InboundDecoder] = WeakSet()


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
    owner_id: UUID | None = None
    lease_expires_at: datetime | None = None
    fence_token: int = Field(default=0, ge=0)


class DeliveryReservation(BaseModel):
    """The atomic outcome of a repository reservation transaction."""

    model_config = ConfigDict(frozen=True)

    action: ReservationAction
    result: DeliveryResult | None = None
    fence_token: int = Field(default=0, ge=0)


class MessageDeliveryRepository(Protocol):
    """PostgreSQL source-of-truth boundary for atomic, durable idempotency state."""

    async def reserve(self, command: MessageCommand) -> DeliveryReservation:
        """Atomically persist a pending record before one caller may send."""

    async def wait(self, command: MessageCommand) -> None:
        """Wait for the winning sender to complete or declare an uncertain outcome."""

    async def complete(self, command: MessageCommand, reservation: DeliveryReservation, result: DeliveryResult | None) -> bool:
        """Atomically persist the safe terminal result and release duplicate waiters."""

    async def mark_uncertain(self, command: MessageCommand, reservation: DeliveryReservation, error_code: TelegramErrorCode) -> bool:
        """Persist ambiguity before a caller attempts remote reconciliation."""

    async def renew(self, command: MessageCommand, reservation: DeliveryReservation) -> bool:
        """Extend this exact outbound owner/fence lease while a remote call remains live."""

    async def allow_resend_after_reconcile_miss(self, command: MessageCommand, reservation: DeliveryReservation) -> DeliveryReservation:
        """Atomically make one reconciled-miss caller the next sender."""


class InMemoryMessageDeliveryRepository:
    """Explicit test fake; production must use ``SqlAlchemyMessageDeliveryRepository``."""

    def __init__(self, records: tuple[DeliveryRecord, ...] = (), *, clock: Callable[[], datetime] | None = None, lease_seconds: float = 30) -> None:
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
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_seconds = lease_seconds
        self._owner_id = uuid4()

    @property
    def lease_seconds(self) -> float:
        return self._lease_seconds

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
                    owner_id=self._owner_id,
                    lease_expires_at=self._now() + timedelta(seconds=self._lease_seconds),
                    fence_token=1,
                )
                self._events[key] = asyncio.Event()
                return DeliveryReservation(action="send", fence_token=1)
            if record.peer_id != command.peer_id:
                return DeliveryReservation(action="completed", result=None, fence_token=record.fence_token)
            if record.state == "completed":
                return DeliveryReservation(action="completed", result=record.result, fence_token=record.fence_token)
            if record.state == "pending" and (record.lease_expires_at is None or record.lease_expires_at <= self._now()):
                self._records[key] = record.model_copy(update={"state": "uncertain", "owner_id": None, "lease_expires_at": None})
                self._events[key].set()
                return DeliveryReservation(action="reconcile", fence_token=record.fence_token)
            if record.state == "uncertain":
                return DeliveryReservation(action="reconcile", fence_token=record.fence_token)
            return DeliveryReservation(action="wait", fence_token=record.fence_token)

    async def wait(self, command: MessageCommand) -> None:
        await asyncio.sleep(0)

    async def complete(self, command: MessageCommand, reservation: DeliveryReservation, result: DeliveryResult | None) -> bool:
        if result is None:
            return False
        key = self._key(command)
        async with self._lock:
            current = self._records.get(key)
            if current is None:
                return False
            if current.state == "pending" and (current.owner_id != self._owner_id or current.fence_token != reservation.fence_token):
                return False
            if current.state not in {"pending", "uncertain"}:
                return False
            self._records[key] = current.model_copy(update={"state": "completed", "result": result, "error_code": None})
            self._events[key].set()
            return True

    async def mark_uncertain(self, command: MessageCommand, reservation: DeliveryReservation, error_code: TelegramErrorCode) -> bool:
        key = self._key(command)
        async with self._lock:
            current = self._records.get(key)
            if current is None or current.state != "pending" or current.owner_id != self._owner_id or current.fence_token != reservation.fence_token:
                return False
            self._records[key] = current.model_copy(update={"state": "uncertain", "error_code": error_code, "owner_id": None, "lease_expires_at": None})
            self._events[key].set()
            return True

    async def renew(self, command: MessageCommand, reservation: DeliveryReservation) -> bool:
        key = self._key(command)
        async with self._lock:
            current = self._records.get(key)
            if current is None or current.state != "pending" or current.owner_id != self._owner_id or current.fence_token != reservation.fence_token:
                return False
            self._records[key] = current.model_copy(update={"lease_expires_at": self._now() + timedelta(seconds=self._lease_seconds)})
            return True

    async def allow_resend_after_reconcile_miss(self, command: MessageCommand, reservation: DeliveryReservation) -> DeliveryReservation:
        key = self._key(command)
        async with self._lock:
            current = self._records.get(key)
            if current is None:
                raise KeyError("idempotency reservation was not found")
            if current.state == "completed":
                return DeliveryReservation(action="completed", result=current.result, fence_token=current.fence_token)
            if current.state == "uncertain":
                fence = current.fence_token + 1
                self._records[key] = current.model_copy(update={"state": "pending", "owner_id": self._owner_id, "lease_expires_at": self._now() + timedelta(seconds=self._lease_seconds), "fence_token": fence})
                self._events[key] = asyncio.Event()
                return DeliveryReservation(action="send", fence_token=fence)
            return DeliveryReservation(action="wait", fence_token=current.fence_token)

    async def record(self, command: MessageCommand) -> DeliveryRecord | None:
        """Test-only safe inspection; it returns no raw message text."""
        return self._records.get(self._key(command))

    @staticmethod
    def _key(command: MessageCommand) -> tuple[UUID, str]:
        return command.account_id, command.idempotency_key

    def _now(self) -> datetime:
        value = self._clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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
        remote_deduplication: object | None = None,
        inbound_decoder: object | None = None,
        outbound_deadline_seconds: float | None = None,
        lease_renew_seconds: float | None = None,
    ) -> None:
        lease = getattr(repository, "lease_seconds", 30.0)
        outbound_deadline_seconds = outbound_deadline_seconds or lease * 0.75
        lease_renew_seconds = lease_renew_seconds or lease * 0.25
        if outbound_deadline_seconds <= 0 or lease_renew_seconds <= 0 or lease_renew_seconds * 2 >= lease or outbound_deadline_seconds >= lease:
            raise ValueError("invalid outbound lease timing")
        self._client = client
        self._repository = repository
        self._compatibility = compatibility
        self._adapter = adapter
        self._adapter_version = adapter_version
        self._proxy_id = proxy_id
        self._connection_is_active = connection_is_active
        self._outbound_deadline_seconds = outbound_deadline_seconds
        self._lease_renew_seconds = lease_renew_seconds
        self._body_vault = _EphemeralBodyVault()
        self._inbound_issuer = self._bind_inbound_decoder(inbound_decoder)
        self._remote_deduplication = remote_deduplication

    def create_command(
        self, *, account_id: UUID, peer_id: int, idempotency_key: str, synthetic_body: object
    ) -> MessageCommand:
        """Place synthetic text in this gateway's ephemeral vault and return safe metadata."""
        return MessageCommand.create(
            account_id=account_id,
            peer_id=peer_id,
            idempotency_key=idempotency_key,
            body_handle=self._body_vault.store(synthetic_body),
        )

    async def send(self, command: MessageCommand) -> DeliveryResult:
        """Return one durable result; ambiguous sends always reconcile before a later resend."""
        if not self._connection_is_active():
            self._record("connection_inactive")
            raise TelegramGatewayError("connection_inactive")
        while True:
            reservation = await self._repository.reserve(command)
            if reservation.action == "completed":
                self._body_vault.discard(command.body_handle)
                if reservation.result is None:
                    raise TelegramGatewayError("invalid_peer")
                return reservation.result
            if reservation.action == "wait":
                await self._repository.wait(command)
                continue
            if reservation.action == "reconcile":
                reconciled = await self._reconcile(command, reservation)
                if reconciled is not None:
                    return reconciled
                if not self._has_approved_remote_deduplication():
                    self._record("telegram_unknown")
                    raise TelegramGatewayError("telegram_unknown")
                reservation = await self._repository.allow_resend_after_reconcile_miss(command, reservation)
                if reservation.action == "completed" and reservation.result is not None:
                    self._body_vault.discard(command.body_handle)
                    return reservation.result
                if reservation.action == "wait":
                    await self._repository.wait(command)
                    continue
            return await self._send_reserved(command, reservation)

    def normalize_incoming(self, event: object) -> TelegramUpdate | None:
        """Default-deny everything except an authenticated private non-service user envelope."""
        if self._inbound_issuer is None or not isinstance(event, _InboundCapability) or event._issuer is not self._inbound_issuer:
            return None
        return TelegramUpdate(
            update_id=event.update_id,
            sender_id=event.sender_id,
            peer_id=event.peer_id,
            received_at=event.received_at,
        )

    async def _send_reserved(self, command: MessageCommand, reservation: DeliveryReservation) -> DeliveryResult:
        try:
            external_message_id = await self._send_with_renewal(command, reservation)
        except asyncio.CancelledError:
            # Cancellation can arrive after the adapter started its network
            # effect. Persist ambiguity under shield before allowing teardown.
            uncertainty = asyncio.create_task(self._repository.mark_uncertain(command, reservation, "timeout"))
            try:
                await asyncio.shield(uncertainty)
            except asyncio.CancelledError:
                await asyncio.shield(uncertainty)
                raise
            raise
        except Exception as error:
            code = map_telegram_error(error)
            await self._persist_uncertain(command, reservation, code)
            reconciled = await self._reconcile(command, reservation)
            if reconciled is not None:
                return reconciled
            self._record(code)
            raise TelegramGatewayError(code) from None
        result = DeliveryResult(external_message_id=str(external_message_id), outcome="sent")
        if not await self._repository.complete(command, reservation, result):
            reconciled = await self._reconcile(command, reservation)
            if reconciled is not None:
                return reconciled
            raise TelegramGatewayError("telegram_unknown")
        self._body_vault.discard(command.body_handle)
        self._record("sent")
        return result

    async def _send_with_renewal(self, command: MessageCommand, reservation: DeliveryReservation) -> str:
        task = asyncio.create_task(
            self._client.send_message(command.peer_id, self._body_vault.read(command.body_handle), command.idempotency_key)
        )
        deadline = asyncio.get_running_loop().time() + self._outbound_deadline_seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await self._persist_uncertain(command, reservation, "timeout")
                task.add_done_callback(_consume_task_exception)
                raise TelegramGatewayError("timeout")
            done, _ = await asyncio.wait({task}, timeout=min(remaining, self._lease_renew_seconds))
            if done:
                return task.result()
            if not await self._repository.renew(command, reservation):
                await self._persist_uncertain(command, reservation, "timeout")
                task.add_done_callback(_consume_task_exception)
                raise TelegramGatewayError("timeout")

    async def _reconcile(self, command: MessageCommand, reservation: DeliveryReservation) -> DeliveryResult | None:
        try:
            external_message_id = await self._client.reconcile_message(command.peer_id, command.idempotency_key)
        except Exception as error:
            code = map_telegram_error(error)
            self._record(code)
            raise TelegramGatewayError(code) from None
        if external_message_id is None:
            return None
        result = DeliveryResult(external_message_id=str(external_message_id), outcome="reconciled")
        # Reconciliation completes regardless of the prior pending owner; a
        # remote hit is definitive and must fence every later resend.
        if not await self._repository.complete(command, reservation, result):
            self._record("telegram_unknown")
            raise TelegramGatewayError("telegram_unknown")
        self._body_vault.discard(command.body_handle)
        self._record("reconciled")
        return result

    async def _persist_uncertain(self, command: MessageCommand, reservation: DeliveryReservation, code: TelegramErrorCode) -> None:
        persistence = asyncio.create_task(self._repository.mark_uncertain(command, reservation, code))
        try:
            await asyncio.shield(persistence)
        except asyncio.CancelledError:
            await asyncio.shield(persistence)
            raise

    def _record(self, outcome: CompatibilityOutcome) -> None:
        self._compatibility.record(
            adapter=self._adapter,
            adapter_version=self._adapter_version,
            proxy=self._proxy_id,
            outcome=outcome,
        )

    def _has_approved_remote_deduplication(self) -> bool:
        capability = self._remote_deduplication
        return (
            isinstance(capability, _RemoteDeduplicationCapability)
            and capability in _ISSUED_REMOTE_DEDUPLICATION_CAPABILITIES
            and capability.client is self._client
            and capability.adapter == self._adapter
            and capability.adapter_version == self._adapter_version
        )

    @staticmethod
    def _bind_inbound_decoder(decoder: object | None) -> object | None:
        if not isinstance(decoder, _InboundDecoder) or decoder not in _ISSUED_INBOUND_DECODERS:
            return None
        try:
            return decoder._bind_gateway()
        except ValueError:
            return None


def _consume_task_exception(task: asyncio.Task[object]) -> None:
    if not task.cancelled():
        try:
            task.exception()
        except Exception:
            pass
