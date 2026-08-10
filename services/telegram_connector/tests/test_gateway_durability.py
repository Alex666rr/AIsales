"""Durability contracts shared by independent SQLAlchemy gateway repositories."""

import asyncio
import copy
import inspect
import pickle
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import telegram_connector as public_api
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from telegram_connector import (
    CompatibilityRegistry,
    InMemoryMessageDeliveryRepository,
    MessageCommand,
    SqlAlchemyCompatibilityRegistry,
    SqlAlchemyMessageDeliveryRepository,
    TelegramGateway,
    TelegramGatewayError,
    create_gateway_schema,
)
from telegram_connector.gateway import _RegisteredSyntheticAdapter, _compose_registered_gateway


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 10, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class Client:
    remote_idempotency_guaranteed = True

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


def command(service: TelegramGateway | None = None) -> MessageCommand:
    if service is not None:
        return service.create_command(
            account_id=UUID(int=1), peer_id=42, idempotency_key="durable-key", synthetic_body="synthetic-only"
        )
    return MessageCommand(account_id=UUID(int=1), peer_id=42, idempotency_key="durable-key", body_handle=UUID(int=99))


def database(path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)
    create_gateway_schema(engine)
    return sessionmaker(engine, expire_on_commit=False)


def assert_body_was_discarded(service: TelegramGateway, protected: MessageCommand) -> None:
    with pytest.raises(TelegramGatewayError):
        service._body_vault.read(protected.body_handle)


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
        initial = asyncio.create_task(first.send(command(first)))
        while client.send_count != 1:
            if initial.done():
                initial.result()
            await asyncio.sleep(0)
        duplicate = asyncio.create_task(second.send(command(second)))
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
        protected = command(service)
        sending = asyncio.create_task(service.send(protected))
        await entered.wait()
        sending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sending
        assert_body_was_discarded(service, protected)
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
    service, decoder = _compose_registered_gateway(
        adapter=_RegisteredSyntheticAdapter(Client()),
        repository=InMemoryMessageDeliveryRepository(),
        compatibility=CompatibilityRegistry(),
        proxy_id=None,
        connection_is_active=lambda: True,
    )
    trusted = decoder.private_user(
        update_id=7, sender_id=42, peer_id=42, received_at=datetime(2026, 8, 10, tzinfo=UTC)
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
    service = TelegramGateway(
        client=Client(), repository=InMemoryMessageDeliveryRepository(), compatibility=CompatibilityRegistry(),
        adapter="synthetic", adapter_version="1", proxy_id=None, connection_is_active=lambda: True,
    )
    with pytest.raises(ValueError) as failure:
        service.create_command(
            account_id=UUID(int=1), peer_id=42, idempotency_key="safe-key", synthetic_body=sentinel,
        )
    assert sentinel not in str(failure.value)
    assert sentinel not in repr(failure.value)


def test_rejected_command_metadata_does_not_strand_its_body_in_the_vault():
    """Storing text before command validation leaves an unreachable raw body after validation fails."""
    service = TelegramGateway(
        client=Client(), repository=InMemoryMessageDeliveryRepository(), compatibility=CompatibilityRegistry(),
        adapter="synthetic", adapter_version="1", proxy_id=None, connection_is_active=lambda: True,
    )
    with pytest.raises(ValueError):
        service.create_command(
            account_id=UUID(int=1), peer_id=-1, idempotency_key="safe-key", synthetic_body="synthetic-only",
        )
    assert service._body_vault._bodies == {}


def test_lookup_miss_without_remote_deduplication_never_resends_after_expired_lease():
    """An expired local lease cannot prove the first Telegram RPC stopped, so a lookup miss is unsafe to resend."""
    async def scenario() -> None:
        clock = Clock()
        repository = InMemoryMessageDeliveryRepository(clock=clock.now, lease_seconds=1)
        first = await repository.reserve(command())
        assert first.action == "send"
        clock.value += timedelta(seconds=2)
        recovered = await repository.reserve(command())
        assert recovered.action == "reconcile"

        class LookupMissClient(Client):
            remote_idempotency_guaranteed = False

        client = LookupMissClient()
        gateway = TelegramGateway(
            client=client, repository=repository, compatibility=CompatibilityRegistry(),
            adapter="synthetic", adapter_version="1", proxy_id=None, connection_is_active=lambda: True,
        )
        protected = command(gateway)
        with pytest.raises(Exception):
            await gateway.send(protected)
        assert client.send_count == 0
        assert_body_was_discarded(gateway, protected)

    asyncio.run(scenario())


def test_timeout_and_reconciliation_miss_discard_the_gateway_body_handle():
    """A timed-out live RPC must not leave its raw body in the gateway vault after return."""
    async def scenario() -> None:
        release = asyncio.Event()
        client = Client(release)
        service = TelegramGateway(
            client=client,
            repository=InMemoryMessageDeliveryRepository(lease_seconds=1),
            compatibility=CompatibilityRegistry(),
            adapter="synthetic",
            adapter_version="1",
            proxy_id=None,
            connection_is_active=lambda: True,
            outbound_deadline_seconds=0.01,
            lease_renew_seconds=0.001,
        )
        protected = command(service)
        with pytest.raises(TelegramGatewayError):
            await service.send(protected)
        assert client.reconcile_count == 1
        assert_body_was_discarded(service, protected)
        release.set()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_failed_completion_discards_the_gateway_body_handle():
    """A repository completion rejection must not retain text after reconciliation also fails."""
    async def scenario() -> None:
        class RefusingCompletionRepository(InMemoryMessageDeliveryRepository):
            async def complete(self, command, reservation, result):
                return False

        client = Client()
        service = TelegramGateway(
            client=client,
            repository=RefusingCompletionRepository(),
            compatibility=CompatibilityRegistry(),
            adapter="synthetic",
            adapter_version="1",
            proxy_id=None,
            connection_is_active=lambda: True,
        )
        protected = command(service)
        with pytest.raises(TelegramGatewayError):
            await service.send(protected)
        assert_body_was_discarded(service, protected)

    asyncio.run(scenario())


def test_public_callers_cannot_mint_remote_or_inbound_authority():
    """Public registries or constructor grants let an arbitrary client authorize its own effects."""
    assert not hasattr(public_api, "ApprovedAdapterRegistry")
    assert "remote_deduplication" not in inspect.signature(TelegramGateway).parameters
    assert "inbound_decoder" not in inspect.signature(TelegramGateway).parameters

    common = dict(
        client=Client(),
        repository=InMemoryMessageDeliveryRepository(),
        compatibility=CompatibilityRegistry(),
        adapter="synthetic",
        adapter_version="1",
        proxy_id=None,
        connection_is_active=lambda: True,
    )
    with pytest.raises(TypeError):
        TelegramGateway(**common, remote_deduplication=object())
    with pytest.raises(TypeError):
        TelegramGateway(**common, inbound_decoder=object())


def test_delayed_first_send_and_replacement_key_produce_one_remote_effect_after_lease_expiry():
    """A replacement is safe only when both calls use one remote key that the adapter definitively deduplicates."""
    async def scenario() -> None:
        clock = Clock()
        repository = InMemoryMessageDeliveryRepository(clock=clock.now, lease_seconds=1)
        first_reservation = await repository.reserve(command())
        assert first_reservation.action == "send"
        clock.value += timedelta(seconds=2)

        class KeyDeduplicatingClient(Client):
            remote_idempotency_guaranteed = True

            async def send_message(self, peer_id, message_text, idempotency_key):
                self.send_count += 1
                self.remote.setdefault(idempotency_key, "remote-once")
                return self.remote[idempotency_key]

            async def reconcile_message(self, peer_id, idempotency_key):
                return None

        client = KeyDeduplicatingClient()
        service, _ = _compose_registered_gateway(
            adapter=_RegisteredSyntheticAdapter(client),
            repository=repository,
            compatibility=CompatibilityRegistry(),
            proxy_id=None,
            connection_is_active=lambda: True,
        )
        result = await service.send(command(service))
        assert result.external_message_id == "remote-once"
        assert len(client.remote) == 1

    asyncio.run(scenario())


def test_inbound_capability_is_bound_to_issuing_gateway_and_rejects_forgery():
    """A caller-constructible inbound model allows forged private-user provenance."""
    one, one_decoder = _compose_registered_gateway(
        adapter=_RegisteredSyntheticAdapter(Client()), repository=InMemoryMessageDeliveryRepository(),
        compatibility=CompatibilityRegistry(), proxy_id=None, connection_is_active=lambda: True,
    )
    two, _ = _compose_registered_gateway(
        adapter=_RegisteredSyntheticAdapter(Client()), repository=InMemoryMessageDeliveryRepository(),
        compatibility=CompatibilityRegistry(), proxy_id=None, connection_is_active=lambda: True,
    )
    capability = one_decoder.private_user(update_id=3, sender_id=42, peer_id=42, received_at=datetime(2026, 8, 10, tzinfo=UTC))
    assert one.normalize_incoming(capability) is not None
    assert two.normalize_incoming(capability) is None
    forged = capability.__class__(3, 42, 42, datetime(2026, 8, 10, tzinfo=UTC))
    assert one.normalize_incoming(forged) is None


def test_message_command_pickle_and_state_export_fail_without_body_sentinel():
    """Private Pydantic attributes can still leak through pickle or explicit state unless disabled."""
    sentinel = "RAW-TELEGRAM-PICKLE-SENTINEL"
    protected = TelegramGateway(client=Client(), repository=InMemoryMessageDeliveryRepository(), compatibility=CompatibilityRegistry(), adapter="synthetic", adapter_version="1", proxy_id=None, connection_is_active=lambda: True).create_command(account_id=UUID(int=1), peer_id=42, idempotency_key="pickle-key", synthetic_body=sentinel)
    for operation in (lambda: pickle.dumps(protected), protected.__getstate__):
        with pytest.raises(Exception) as failure:
            operation()
        assert sentinel not in str(failure.value)
    assert sentinel not in repr(protected.model_copy().model_dump())


def test_message_command_keeps_raw_body_only_in_gateway_vault_and_not_object_state():
    """Keeping text in Pydantic private state leaks it through ordinary object inspection/copies."""
    sentinel = "RAW-TELEGRAM-VAULT-SENTINEL"
    protected = TelegramGateway(client=Client(), repository=InMemoryMessageDeliveryRepository(), compatibility=CompatibilityRegistry(), adapter="synthetic", adapter_version="1", proxy_id=None, connection_is_active=lambda: True).create_command(account_id=UUID(int=1), peer_id=42, idempotency_key="vault-key", synthetic_body=sentinel)

    exposed = repr(protected.__dict__) + repr(getattr(protected, "__pydantic_private__", {}))
    assert sentinel not in exposed
    for operation in (lambda: copy.copy(protected), lambda: copy.deepcopy(protected)):
        copied = operation()
        assert sentinel not in repr(copied.__dict__)
        assert sentinel not in repr(getattr(copied, "__pydantic_private__", {}))


def test_counterfeit_remote_deduplication_capability_is_fail_closed_after_uncertainty():
    """An arbitrary adapter identity cannot enter the private trusted composition path."""
    async def scenario() -> None:
        clock = Clock()
        repository = InMemoryMessageDeliveryRepository(clock=clock.now, lease_seconds=1)
        assert (await repository.reserve(command())).action == "send"
        clock.value += timedelta(seconds=2)
        client = Client()
        with pytest.raises(TypeError):
            _compose_registered_gateway(
                adapter=client, repository=repository, compatibility=CompatibilityRegistry(),
                proxy_id=None, connection_is_active=lambda: True,
            )
        assert client.send_count == 0

    asyncio.run(scenario())


def test_live_expired_sender_and_recovery_share_one_remote_deduplicated_effect(tmp_path):
    """A live old RPC and lease-recovery RPC must share a remote key with exactly one effect."""
    async def scenario() -> None:
        sessions = database(tmp_path / "two-live-rpcs.db")
        clock = Clock()
        first_repository = SqlAlchemyMessageDeliveryRepository(sessions, clock=clock, lease_seconds=1)
        second_repository = SqlAlchemyMessageDeliveryRepository(sessions, clock=clock, lease_seconds=1)
        entered, release, first_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class RemoteService:
            def __init__(self) -> None:
                self.requests = 0
                self.effects: dict[str, str] = {}

            def effect(self, key: str) -> str:
                self.requests += 1
                return self.effects.setdefault(key, "remote-effect-1")

            @property
            def effect_count(self) -> int:
                return len(self.effects)

        remote = RemoteService()

        class DelayedCancellationResistantClient(Client):
            def __init__(self, *, delayed: bool) -> None:
                super().__init__()
                self.delayed = delayed

            async def send_message(self, peer_id: int, message_text: str, idempotency_key: str) -> str:
                self.send_count += 1
                assert message_text == "synthetic-only"
                if self.delayed:
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        await release.wait()
                result = remote.effect(idempotency_key)
                first_finished.set()
                return result

            async def reconcile_message(self, peer_id: int, idempotency_key: str) -> str | None:
                self.reconcile_count += 1
                return remote.effects.get(idempotency_key)

        old_client, recovery_client = DelayedCancellationResistantClient(delayed=True), DelayedCancellationResistantClient(delayed=False)
        first, _ = _compose_registered_gateway(
            adapter=_RegisteredSyntheticAdapter(old_client), repository=first_repository,
            compatibility=CompatibilityRegistry(), proxy_id=None, connection_is_active=lambda: True,
            outbound_deadline_seconds=0.5, lease_renew_seconds=0.1,
        )
        recovered, _ = _compose_registered_gateway(
            adapter=_RegisteredSyntheticAdapter(recovery_client), repository=second_repository,
            compatibility=CompatibilityRegistry(), proxy_id=None, connection_is_active=lambda: True,
            outbound_deadline_seconds=0.5, lease_renew_seconds=0.1,
        )
        original = asyncio.create_task(first.send(command(first)))
        await entered.wait()
        clock.value += timedelta(seconds=2)
        replacement = await recovered.send(command(recovered))
        assert replacement.external_message_id == "remote-effect-1"
        release.set()
        await first_finished.wait()
        with pytest.raises(Exception):
            await original
        assert remote.effect_count == 1
        assert remote.effects["durable-key"] == replacement.external_message_id

    asyncio.run(scenario())
