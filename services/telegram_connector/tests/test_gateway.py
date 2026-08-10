"""Network-free contracts for the safe, idempotent Telegram message gateway."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest

from telegram_connector import (
    CompatibilityRegistry,
    InMemoryMessageDeliveryRepository,
    MessageCommand,
    TelegramGateway,
    TelegramGatewayError,
    TrustedIncomingUpdate,
    TrustedTelegramEntity,
    TrustedTelegramEntityKind,
)


class SyntheticClient:
    """Injected client fake; it never contacts Telegram."""

    def __init__(self, *, send_gate: asyncio.Event | None = None, send_error: Exception | None = None) -> None:
        self.send_gate = send_gate
        self.send_error = send_error
        self.send_count = 0
        self.reconcile_count = 0
        self.remote: dict[str, str] = {}

    async def send_message(self, peer_id: int, message_text: str, idempotency_key: str) -> str:
        self.send_count += 1
        if self.send_gate is not None:
            await self.send_gate.wait()
        if self.send_error is not None:
            raise self.send_error
        message_id = f"synthetic-{self.send_count}"
        self.remote[idempotency_key] = message_id
        return message_id

    async def reconcile_message(self, peer_id: int, idempotency_key: str) -> str | None:
        self.reconcile_count += 1
        return self.remote.get(idempotency_key)


def command(*, idempotency_key: str = "fixed-key") -> MessageCommand:
    return MessageCommand.create(
        account_id=UUID(int=10),
        peer_id=42,
        idempotency_key=idempotency_key,
        synthetic_body="synthetic-only-message",
    )


def gateway(client: SyntheticClient | None = None, repository=None) -> TelegramGateway:
    return TelegramGateway(
        client=client or SyntheticClient(),
        repository=repository or InMemoryMessageDeliveryRepository(),
        compatibility=CompatibilityRegistry(),
        adapter="synthetic",
        adapter_version="1.0",
        proxy_id=UUID(int=20),
        connection_is_active=lambda: True,
    )


def test_send_returns_same_result_for_same_idempotency_key():
    """Removing durable completion lookup would make a retried command send twice."""
    async def scenario() -> None:
        client = SyntheticClient()
        service = gateway(client)

        first = await service.send(command())
        second = await service.send(command())

        assert first.external_message_id == second.external_message_id == "synthetic-1"
        assert client.send_count == 1

    asyncio.run(scenario())


def test_concurrent_duplicate_sends_coalesce_at_the_atomic_repository_boundary():
    """Replacing the atomic reservation with local locks would permit duplicate process sends."""
    async def scenario() -> None:
        release_send = asyncio.Event()
        client = SyntheticClient(send_gate=release_send)
        repository = InMemoryMessageDeliveryRepository()
        first_gateway = gateway(client, repository)
        second_gateway = gateway(client, repository)

        first_task = asyncio.create_task(first_gateway.send(command()))
        while client.send_count != 1:
            await asyncio.sleep(0)
        second_task = asyncio.create_task(second_gateway.send(command()))
        await asyncio.sleep(0)
        assert client.send_count == 1

        release_send.set()
        first, second = await asyncio.gather(first_task, second_task)
        assert first.external_message_id == second.external_message_id == "synthetic-1"
        assert client.send_count == 1

    asyncio.run(scenario())


def test_restart_reconciles_an_uncertain_send_before_any_resend():
    """A timeout followed by resend without reconciliation can duplicate a real Telegram message."""
    async def scenario() -> None:
        repository = InMemoryMessageDeliveryRepository()
        timed_out = SyntheticClient(send_error=TimeoutError("raw-message-must-not-leak"))
        first = gateway(timed_out, repository)
        with pytest.raises(TelegramGatewayError) as failure:
            await first.send(command())
        assert failure.value.code == "timeout"
        assert "raw-message-must-not-leak" not in str(failure.value)

        restarted_client = SyntheticClient()
        restarted_client.remote["fixed-key"] = "reconciled-7"
        restarted = gateway(restarted_client, repository)
        result = await restarted.send(command())

        assert result.external_message_id == "reconciled-7"
        assert restarted_client.reconcile_count == 1
        assert restarted_client.send_count == 0

    asyncio.run(scenario())


def test_restart_treats_an_orphaned_pending_reservation_as_uncertain():
    """A crashed sender left pending forever would make restart hang instead of reconciling safely."""
    async def scenario() -> None:
        persisted = InMemoryMessageDeliveryRepository()
        assert (await persisted.reserve(command())).action == "send"
        record = await persisted.record(command())
        assert record is not None

        restarted_repository = InMemoryMessageDeliveryRepository((record,))
        restarted_client = SyntheticClient()
        restarted_client.remote["fixed-key"] = "recovered-8"
        result = await gateway(restarted_client, restarted_repository).send(command())

        assert result.external_message_id == "recovered-8"
        assert restarted_client.send_count == 0
        assert restarted_client.reconcile_count == 1

    asyncio.run(scenario())


def test_gateway_rejects_inactive_connection_without_a_network_effect():
    """Sending through a non-active lifecycle state bypasses the connection safety boundary."""
    async def scenario() -> None:
        client = SyntheticClient()
        service = TelegramGateway(
            client=client,
            repository=InMemoryMessageDeliveryRepository(),
            compatibility=CompatibilityRegistry(),
            adapter="synthetic",
            adapter_version="1.0",
            proxy_id=None,
            connection_is_active=lambda: False,
        )
        with pytest.raises(TelegramGatewayError) as failure:
            await service.send(command())
        assert failure.value.code == "connection_inactive"
        assert client.send_count == 0

    asyncio.run(scenario())


def test_incoming_normalizes_private_user_metadata_without_exposing_raw_text():
    """Including Telegram text in a normalized update would expose content to later paths."""
    service = gateway()
    update = service.normalize_incoming(
        TrustedIncomingUpdate(
            update_id=9,
            peer=TrustedTelegramEntity(entity_id=42, kind=TrustedTelegramEntityKind.USER),
            sender=TrustedTelegramEntity(entity_id=42, kind=TrustedTelegramEntityKind.USER),
            is_service=False,
            received_at=datetime(2026, 8, 7, tzinfo=UTC),
        )
    )

    assert update is not None
    assert (update.update_id, update.sender_id, update.peer_id) == (9, 42, 42)
    serialized = update.model_dump_json()
    assert "synthetic-only-message" not in serialized


@pytest.mark.parametrize(
    "peer_kind,sender_kind,is_service",
    [
        (TrustedTelegramEntityKind.GROUP, TrustedTelegramEntityKind.USER, False),
        (TrustedTelegramEntityKind.SUPERGROUP, TrustedTelegramEntityKind.USER, False),
        (TrustedTelegramEntityKind.CHANNEL, TrustedTelegramEntityKind.USER, False),
        (TrustedTelegramEntityKind.USER, TrustedTelegramEntityKind.SERVICE, False),
        (TrustedTelegramEntityKind.USER, TrustedTelegramEntityKind.BOT, False),
        (TrustedTelegramEntityKind.UNKNOWN, TrustedTelegramEntityKind.USER, False),
        (TrustedTelegramEntityKind.USER, TrustedTelegramEntityKind.USER, True),
    ],
)
def test_incoming_defaults_to_deny_for_non_private_or_malformed_events(peer_kind, sender_kind, is_service):
    """Loosening the classifier would allow group, service, bot, or malformed traffic through."""
    event = TrustedIncomingUpdate(
        update_id=9,
        peer=TrustedTelegramEntity(entity_id=42, kind=peer_kind),
        sender=TrustedTelegramEntity(entity_id=42, kind=sender_kind),
        is_service=is_service,
        received_at=datetime(2026, 8, 7, tzinfo=UTC),
    )
    assert gateway().normalize_incoming(event) is None


def test_commands_registry_and_failures_redact_message_and_proxy_credentials():
    """Serializing protected inputs or adapter errors would leak content and credentials."""
    from telegram_connector import ProxyConfig

    protected = command()
    proxy = ProxyConfig(proxy_id=UUID(int=20), url="socks5://user:proxy-password@edge.example:1080")
    registry = CompatibilityRegistry()
    row = registry.record(adapter="synthetic", adapter_version="1.0", proxy=proxy, outcome="sent")

    exposed = f"{protected!r} {protected.model_dump_json()} {row!r} {row.model_dump_json()} {proxy!r} {proxy.model_dump_json()}"
    assert "synthetic-only-message" not in exposed
    assert "proxy-password" not in exposed
    assert "user" not in row.model_dump_json()


def test_compatibility_registry_keeps_one_safe_row_per_adapter_version_proxy_combination():
    """Appending every send would violate the one-row compatibility evidence contract."""
    registry = CompatibilityRegistry()
    registry.record(adapter="synthetic", adapter_version="1.0", proxy=UUID(int=20), outcome="sent")
    latest = registry.record(adapter="synthetic", adapter_version="1.0", proxy=UUID(int=20), outcome="reconciled")

    assert registry.records() == (latest,)
    assert latest.outcome == "reconciled"
