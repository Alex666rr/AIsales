"""Durability contracts shared by independent SQLAlchemy gateway repositories."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from telegram_connector import (
    CompatibilityRegistry,
    InMemoryMessageDeliveryRepository,
    MessageCommand,
    SqlAlchemyCompatibilityRegistry,
    SqlAlchemyMessageDeliveryRepository,
    TelegramGateway,
    TrustedIncomingUpdate,
    TrustedTelegramEntity,
    TrustedTelegramEntityKind,
    create_gateway_schema,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 10, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class Client:
    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.send_count = 0
        self.reconcile_count = 0
        self.remote: dict[str, str] = {}

    async def send_message(self, peer_id: int, message_text: str, idempotency_key: str) -> str:
        self.send_count += 1
        if self.gate is not None:
            await self.gate.wait()
        self.remote[idempotency_key] = f"remote-{self.send_count}"
        return self.remote[idempotency_key]

    async def reconcile_message(self, peer_id: int, idempotency_key: str) -> str | None:
        self.reconcile_count += 1
        return self.remote.get(idempotency_key)


def command() -> MessageCommand:
    return MessageCommand.create(
        account_id=UUID(int=1), peer_id=42, idempotency_key="durable-key", synthetic_body="synthetic-only"
    )


def database(path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)
    create_gateway_schema(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_two_sqlalchemy_repository_instances_allow_exactly_one_sender(tmp_path):
    """Replacing database reservation with a process dictionary allows duplicate cross-process sends."""
    async def scenario() -> None:
        sessions = database(tmp_path / "gateway.db")
        clock = Clock()
        first_repository = SqlAlchemyMessageDeliveryRepository(sessions, clock=clock, lease_seconds=30)
        second_repository = SqlAlchemyMessageDeliveryRepository(sessions, clock=clock, lease_seconds=30)
        release = asyncio.Event()
        client = Client(release)
        first = TelegramGateway(
            client=client, repository=first_repository, compatibility=CompatibilityRegistry(),
            adapter="synthetic", adapter_version="1", proxy_id=UUID(int=2), connection_is_active=lambda: True,
        )
        second = TelegramGateway(
            client=client, repository=second_repository, compatibility=CompatibilityRegistry(),
            adapter="synthetic", adapter_version="1", proxy_id=UUID(int=2), connection_is_active=lambda: True,
        )
        initial = asyncio.create_task(first.send(command()))
        while client.send_count != 1:
            if initial.done():
                initial.result()
            await asyncio.sleep(0)
        duplicate = asyncio.create_task(second.send(command()))
        await asyncio.sleep(0)
        assert client.send_count == 1
        release.set()
        one, two = await asyncio.gather(initial, duplicate)
        assert one.external_message_id == two.external_message_id == "remote-1"
        assert client.send_count == 1

    asyncio.run(scenario())


def test_expired_pending_lease_reconciles_before_a_single_new_sender_is_reserved(tmp_path):
    """Releasing an expired pending row directly to send can duplicate a crash-ambiguous message."""
    async def scenario() -> None:
        sessions = database(tmp_path / "lease.db")
        clock = Clock()
        first = SqlAlchemyMessageDeliveryRepository(sessions, clock=clock, lease_seconds=1)
        second = SqlAlchemyMessageDeliveryRepository(sessions, clock=clock, lease_seconds=1)
        reserved = await first.reserve(command())
        assert reserved.action == "send"
        clock.value += timedelta(seconds=2)

        expired = await second.reserve(command())
        assert expired.action == "reconcile"
        retry = await second.allow_resend_after_reconcile_miss(command(), expired)
        assert retry.action == "send"
        assert await first.complete(command(), reserved, result=None) is False

    asyncio.run(scenario())


def test_cancelled_network_effect_is_persisted_uncertain_before_restart_reconciles():
    """Letting cancellation escape before durable uncertainty can cause a duplicate resend after restart."""
    async def scenario() -> None:
        entered = asyncio.Event()

        class BlockingClient(Client):
            async def send_message(self, peer_id: int, message_text: str, idempotency_key: str) -> str:
                self.send_count += 1
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        repository = InMemoryMessageDeliveryRepository()
        client = BlockingClient()
        service = TelegramGateway(
            client=client, repository=repository, compatibility=CompatibilityRegistry(),
            adapter="synthetic", adapter_version="1", proxy_id=None, connection_is_active=lambda: True,
        )
        sending = asyncio.create_task(service.send(command()))
        await entered.wait()
        sending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sending
        persisted = await repository.record(command())
        assert persisted is not None and persisted.state == "uncertain"

        restarted = Client()
        restarted.remote["durable-key"] = "recovered"
        result = await TelegramGateway(
            client=restarted, repository=repository, compatibility=CompatibilityRegistry(),
            adapter="synthetic", adapter_version="1", proxy_id=None, connection_is_active=lambda: True,
        ).send(command())
        assert result.external_message_id == "recovered"
        assert restarted.send_count == 0

    asyncio.run(scenario())


def test_sqlalchemy_registry_upserts_one_safe_row_across_instances(tmp_path):
    """A process-local registry cannot enforce one compatibility row across workers."""
    sessions = database(tmp_path / "compatibility.db")
    first = SqlAlchemyCompatibilityRegistry(sessions)
    second = SqlAlchemyCompatibilityRegistry(sessions)

    first.record(adapter="synthetic", adapter_version="1", proxy=UUID(int=2), outcome="sent")
    latest = second.record(adapter="synthetic", adapter_version="1", proxy=UUID(int=2), outcome="reconciled")
    assert first.records() == (latest,)


def test_trusted_entity_update_rejects_spoofed_public_label_object():
    """Accepting untrusted peer_kind strings lets callers label a group event as a private user message."""
    service = TelegramGateway(
        client=Client(), repository=InMemoryMessageDeliveryRepository(), compatibility=CompatibilityRegistry(),
        adapter="synthetic", adapter_version="1", proxy_id=None, connection_is_active=lambda: True,
    )
    trusted = TrustedIncomingUpdate(
        update_id=7,
        peer=TrustedTelegramEntity(entity_id=42, kind=TrustedTelegramEntityKind.USER),
        sender=TrustedTelegramEntity(entity_id=42, kind=TrustedTelegramEntityKind.USER),
        is_service=False,
        received_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert service.normalize_incoming(trusted) is not None

    class Spoof:
        peer_kind = "private"
        sender_kind = "user"
        peer_id = 42
        sender_id = 42
        is_service = False

    assert service.normalize_incoming(Spoof()) is None


def test_message_body_validation_never_includes_raw_sentinel_in_errors():
    """Delegating body validation to Pydantic exposes rejected Telegram text in public validation errors."""
    sentinel = "RAW-TELEGRAM-TEXT-" + "x" * 5000
    with pytest.raises(ValueError) as failure:
        MessageCommand.create(account_id=UUID(int=1), peer_id=42, idempotency_key="safe-key", synthetic_body=sentinel)
    assert sentinel not in str(failure.value)
    assert sentinel not in repr(failure.value)
