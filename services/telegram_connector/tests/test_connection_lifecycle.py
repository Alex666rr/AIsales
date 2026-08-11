"""Lifecycle contracts for injected Telegram clients and persisted connection state."""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from telegram_connector.proxies import InMemoryProxyAssignmentRepository, ProxyConfig, ProxyHealth
from telegram_connector.runtime.connection import (
    AccountBlockedError,
    AuthorizationLostError,
    ConnectionHealth,
    ConnectionRecord,
    FloodWaitError,
    InMemoryConnectionRepository,
)
from telegram_connector.runtime.supervisor import ConnectionSupervisor
from telegram_connector.session_store import SessionRef


class ManualClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 7, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class AdvancingSleeper:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.delays: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.clock.value += timedelta(seconds=seconds)


class PassiveMonitorSleeper:
    async def sleep(self, seconds: float) -> None:
        await asyncio.Event().wait()


class AvailableProxyChecker:
    async def check(self, proxy: ProxyConfig) -> ProxyHealth:
        return ProxyHealth(available=True, ip_address="203.0.113.40", latency_ms=7)


class ScriptedClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.disconnected = False
        self.connects = 0

    async def connect(self) -> None:
        self.connects += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome

    async def is_authorized(self) -> bool:
        return True

    async def disconnect(self) -> None:
        self.disconnected = True


class ScriptedFactory:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.clients: list[ScriptedClient] = []

    async def create(self, session: SessionRef, proxy: ProxyConfig) -> ScriptedClient:
        client = ScriptedClient(self.outcomes)
        self.clients.append(client)
        return client


def make_repository(state: str = "quarantine", repository_clock=None) -> tuple[InMemoryConnectionRepository, SessionRef]:
    session = SessionRef(account_id=UUID(int=10), session_id=UUID(int=11), key_version=1)
    return (
        InMemoryConnectionRepository(
            (
                ConnectionRecord(
                    account_id=UUID(int=10),
                    session_ref=session,
                    health=ConnectionHealth(
                        state=state,
                        last_seen_at=None,
                        proxy_ip=None,
                        latency_ms=None,
                        error_code=None,
                    ),
                ),
            ), clock=repository_clock
        ),
        session,
    )


def make_supervisor(repository, factory, clock, sleeper, *, proxy_repository=None, proxy_checker=None, max_retries=2, monitor_sleeper=None, lease_duration_seconds=60) -> ConnectionSupervisor:
    proxy_repository = proxy_repository or InMemoryProxyAssignmentRepository(
        proxies=(ProxyConfig(proxy_id=UUID(int=1), url="socks5://edge.example:1080", capacity=5),),
        default_proxy_id=UUID(int=1),
    )
    return ConnectionSupervisor(
        repository=repository,
        proxy_repository=proxy_repository,
        proxy_checker=proxy_checker or AvailableProxyChecker(),
        client_factory=factory,
        clock=clock,
        sleeper=sleeper,
        max_retries=max_retries,
        max_backoff_seconds=10,
        monitor_sleeper=monitor_sleeper or PassiveMonitorSleeper(),
        lease_duration_seconds=lease_duration_seconds,
        heartbeat_interval_seconds=lease_duration_seconds / 3,
    )


def test_start_persists_active_health_and_restart_reconnects_from_persisted_state():
    """Using only in-memory health would lose the connection contract after a process restart."""
    async def scenario():
        clock = ManualClock()
        repository, _ = make_repository(repository_clock=clock)
        first_factory = ScriptedFactory([None])
        first = make_supervisor(repository, first_factory, clock, AdvancingSleeper(clock))

        assert (await first.start(UUID(int=10))).state == "active"
        assert (await repository.get(UUID(int=10))).health.state == "active"

        clock.value += timedelta(seconds=61)
        restarted_factory = ScriptedFactory([None])
        restarted = make_supervisor(repository, restarted_factory, clock, AdvancingSleeper(clock))
        assert (await restarted.start(UUID(int=10))).state == "active"
        assert len(restarted_factory.clients) == 1

    asyncio.run(scenario())


def test_failed_new_default_proxy_reservation_is_released_but_preexisting_override_is_preserved():
    """Retaining a failed new shared lease exhausts capacity; releasing an override loses operator intent."""
    class FailingChecker:
        async def check(self, proxy: ProxyConfig) -> ProxyHealth:
            raise RuntimeError("CHECKER-SENTINEL-SECRET")

    async def scenario():
        clock = ManualClock()
        proxy_repository = InMemoryProxyAssignmentRepository(
            proxies=(ProxyConfig(proxy_id=UUID(int=1), url="socks5://edge.example:1080", capacity=1),),
            default_proxy_id=UUID(int=1),
        )
        repository, _ = make_repository()
        supervisor = make_supervisor(
            repository,
            ScriptedFactory([]),
            clock,
            AdvancingSleeper(clock),
            proxy_repository=proxy_repository,
            proxy_checker=FailingChecker(),
        )
        assert (await supervisor.start(UUID(int=10))).error_code == "proxy_unavailable"
        assert await proxy_repository.assignments_for(UUID(int=1)) == ()

        await proxy_repository.set_account_override(UUID(int=10), UUID(int=1))
        repository, _ = make_repository()
        supervisor = make_supervisor(
            repository,
            ScriptedFactory([]),
            clock,
            AdvancingSleeper(clock),
            proxy_repository=proxy_repository,
            proxy_checker=FailingChecker(),
        )
        assert (await supervisor.start(UUID(int=10))).error_code == "proxy_unavailable"
        assert await proxy_repository.assignments_for(UUID(int=1)) == (UUID(int=10),)

    asyncio.run(scenario())


def test_two_supervisors_claim_one_cross_process_connection_lease():
    """Process-local locks alone permit two supervisors sharing a repository to create duplicate clients."""
    class BlockingClient(ScriptedClient):
        def __init__(self, factory):
            super().__init__([None])
            self.factory = factory

        async def connect(self) -> None:
            self.factory.connected.set()
            await self.factory.release.wait()

    class BlockingFactory:
        def __init__(self) -> None:
            self.clients = []
            self.connected = asyncio.Event()
            self.release = asyncio.Event()

        async def create(self, session, proxy):
            client = BlockingClient(self)
            self.clients.append(client)
            return client

    async def scenario():
        clock = ManualClock()
        repository, _ = make_repository(repository_clock=clock)
        factory = BlockingFactory()
        first = make_supervisor(repository, factory, clock, AdvancingSleeper(clock))
        second = make_supervisor(repository, factory, clock, AdvancingSleeper(clock))
        first_start = asyncio.create_task(first.start(UUID(int=10)))
        await factory.connected.wait()

        second_health = await second.start(UUID(int=10))
        assert second_health.state == "quarantine"
        assert len(factory.clients) == 1

        factory.release.set()
        assert (await first_start).state == "active"

    asyncio.run(scenario())


def test_delayed_cancellation_resistant_active_save_cannot_overwrite_pause():
    """A late active write must lose its CAS race after pause invalidates its lease and version."""
    class DelayedActiveSaveRepository(InMemoryConnectionRepository):
        def __init__(self, records):
            super().__init__(records)
            self.active_save_started = asyncio.Event()
            self.release_active_save = asyncio.Event()

        async def save_claimed(self, record, owner_id, *, release_lease):
            if record.health.state == "active":
                self.active_save_started.set()
                try:
                    await self.release_active_save.wait()
                except asyncio.CancelledError:
                    await self.release_active_save.wait()
            return await super().save_claimed(record, owner_id, release_lease=release_lease)

    async def scenario():
        initial, _ = make_repository()
        seed = await initial.get(UUID(int=10))
        assert seed is not None
        repository = DelayedActiveSaveRepository((seed,))
        clock = ManualClock()
        factory = ScriptedFactory([None])
        supervisor = make_supervisor(repository, factory, clock, AdvancingSleeper(clock))
        starting = asyncio.create_task(supervisor.start(UUID(int=10)))
        await repository.active_save_started.wait()

        assert (await supervisor.pause(UUID(int=10))).state == "paused"
        repository.release_active_save.set()
        assert (await starting).state == "paused"
        assert (await supervisor.health(UUID(int=10))).state == "paused"
        assert factory.clients[0].disconnected is True

    asyncio.run(scenario())


def test_live_lease_blocks_second_supervisor_and_remote_pause_closes_owner_client():
    """Releasing a lease at active permits duplicate live clients and hides remote pause from the owner."""
    class GateSleeper:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def sleep(self, seconds: float) -> None:
            self.entered.set()
            await self.release.wait()

    async def scenario():
        repository, _ = make_repository()
        clock = ManualClock()
        proxy_repository = InMemoryProxyAssignmentRepository(
            proxies=(ProxyConfig(proxy_id=UUID(int=1), url="socks5://edge.example:1080", capacity=2),),
            default_proxy_id=UUID(int=1),
        )
        monitor_sleeper = GateSleeper()
        first_factory = ScriptedFactory([None])
        first = make_supervisor(
            repository, first_factory, clock, AdvancingSleeper(clock), proxy_repository=proxy_repository,
            monitor_sleeper=monitor_sleeper, lease_duration_seconds=30,
        )
        assert (await first.start(UUID(int=10))).state == "active"
        await asyncio.sleep(0)
        assert monitor_sleeper.entered.is_set()

        second_factory = ScriptedFactory([None])
        second = make_supervisor(
            repository, second_factory, clock, AdvancingSleeper(clock), proxy_repository=proxy_repository,
            monitor_sleeper=GateSleeper(), lease_duration_seconds=30,
        )
        assert (await second.start(UUID(int=10))).state == "active"
        assert second_factory.clients == []

        assert (await second.pause(UUID(int=10))).state == "paused"
        monitor_sleeper.release.set()
        await asyncio.sleep(0)
        assert first_factory.clients[0].disconnected is True

    asyncio.run(scenario())


def test_expired_fenced_lease_can_be_reclaimed_and_rejects_old_owner_save():
    """Without expiry and a fencing epoch, a crashed worker can either block recovery or write after takeover."""
    async def scenario():
        repository, _ = make_repository()
        clock = ManualClock()
        owner_one = UUID(int=101)
        owner_two = UUID(int=102)
        repository, _ = make_repository(repository_clock=clock)
        first = await repository.try_claim(UUID(int=10), owner_one, lease_seconds=5)
        assert first is not None
        clock.value += timedelta(seconds=6)
        second = await repository.try_claim(UUID(int=10), owner_two, lease_seconds=5)
        assert second is not None
        assert second.fence_token > first.fence_token

        old_active = first.model_copy(update={"health": first.health.model_copy(update={"state": "active"})})
        assert await repository.save_claimed(old_active, owner_one, release_lease=False) is None

    asyncio.run(scenario())


def test_repository_clock_rejects_backdated_owner_after_expiry_and_reclaims_at_boundary():
    """Accepting worker time would let a stalled process renew or save after repository lease expiry."""
    async def scenario():
        clock = ManualClock()
        repository, _ = make_repository(repository_clock=clock)
        first = await repository.try_claim(UUID(int=10), UUID(int=101), lease_seconds=5)
        assert first is not None
        clock.value += timedelta(seconds=5)

        assert await repository.renew_lease(first, UUID(int=101), lease_seconds=5) is None
        assert await repository.save_claimed(first, UUID(int=101), release_lease=False) is None
        second = await repository.try_claim(UUID(int=10), UUID(int=102), lease_seconds=5)
        assert second is not None
        assert second.fence_token > first.fence_token

    asyncio.run(scenario())


def test_stop_releases_default_capacity_but_preserves_override_assignment():
    """Terminal state without assignment release starves another account; releasing override discards intent."""
    async def scenario():
        clock = ManualClock()
        proxy_repository = InMemoryProxyAssignmentRepository(
            proxies=(ProxyConfig(proxy_id=UUID(int=1), url="socks5://edge.example:1080", capacity=1),),
            default_proxy_id=UUID(int=1),
        )
        first_repository, _ = make_repository()
        first = make_supervisor(
            first_repository, ScriptedFactory([None]), clock, AdvancingSleeper(clock), proxy_repository=proxy_repository
        )
        await first.start(UUID(int=10))
        await first.stop(UUID(int=10))

        assert await proxy_repository.reserve_assignment(UUID(int=20)) is not None
        await proxy_repository.release_terminal_assignment(UUID(int=20))

        await proxy_repository.set_account_override(UUID(int=10), UUID(int=1))
        assert await proxy_repository.reserve_assignment(UUID(int=10)) is not None
        await proxy_repository.release_terminal_assignment(UUID(int=10))
        assert await proxy_repository.assignments_for(UUID(int=1)) == (UUID(int=10),)

    asyncio.run(scenario())


def test_exhausted_retry_budget_is_durable_and_a_restart_refuses_to_retry():
    """Leaving exhausted failures quarantined lets a new process silently reset the retry budget."""
    async def scenario():
        repository, _ = make_repository()
        clock = ManualClock()
        first_factory = ScriptedFactory([RuntimeError("transient")])
        first = make_supervisor(repository, first_factory, clock, AdvancingSleeper(clock), max_retries=1)

        exhausted = await first.start(UUID(int=10))
        assert (exhausted.state, exhausted.error_code) == ("blocked", "retry_exhausted")

        restarted_factory = ScriptedFactory([None])
        restarted = make_supervisor(repository, restarted_factory, clock, AdvancingSleeper(clock), max_retries=1)
        assert (await restarted.start(UUID(int=10))).error_code == "retry_exhausted"
        assert restarted_factory.clients == []

    asyncio.run(scenario())


def test_archived_state_is_absorbing_for_pause_stop_start_and_archive():
    """Allowing pause or stop to rewrite archived would resurrect a deliberately retired session."""
    async def scenario():
        repository, _ = make_repository()
        clock = ManualClock()
        supervisor = make_supervisor(repository, ScriptedFactory([]), clock, AdvancingSleeper(clock))

        assert (await supervisor.archive(UUID(int=10))).state == "archived"
        assert (await supervisor.pause(UUID(int=10))).state == "archived"
        assert (await supervisor.stop(UUID(int=10))).state == "archived"
        assert (await supervisor.start(UUID(int=10))).state == "archived"
        assert (await supervisor.archive(UUID(int=10))).state == "archived"

    asyncio.run(scenario())


def test_health_and_retry_timestamps_normalize_to_utc_and_reject_naive_values():
    """Naive persisted timestamps make retry ordering and serialization depend on process locale."""
    offset = datetime(2026, 8, 7, 14, tzinfo=timezone(timedelta(hours=7)))
    health = ConnectionHealth(
        state="active", last_seen_at=offset, proxy_ip=None, latency_ms=None, error_code=None
    )
    record = ConnectionRecord(
        account_id=UUID(int=10),
        session_ref=SessionRef(account_id=UUID(int=10), session_id=UUID(int=11), key_version=1),
        health=health,
        retry_at=offset,
    )

    assert health.last_seen_at == datetime(2026, 8, 7, 7, tzinfo=UTC)
    assert record.retry_at == datetime(2026, 8, 7, 7, tzinfo=UTC)
    assert ConnectionRecord.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValueError):
        ConnectionHealth(state="active", last_seen_at=datetime(2026, 8, 7), proxy_ip=None, latency_ms=None, error_code=None)
    with pytest.raises(ValueError):
        ConnectionRecord(
            account_id=UUID(int=10),
            session_ref=SessionRef(account_id=UUID(int=10), session_id=UUID(int=11), key_version=1),
            health=health,
            retry_at=datetime(2026, 8, 7),
        )


def test_transient_failure_persists_quarantine_before_bounded_reconnect():
    """Skipping the pre-sleep save hides a failed connection from a process that restarts during backoff."""
    async def scenario():
        repository, _ = make_repository()
        clock = ManualClock()
        sleeper = AdvancingSleeper(clock)
        supervisor = make_supervisor(repository, ScriptedFactory([RuntimeError("raw secret"), None]), clock, sleeper)

        health = await supervisor.start(UUID(int=10))

        assert health.state == "active"
        assert sleeper.delays == [1]
        assert [record.health.state for record in repository.history] == ["quarantine", "active"]
        assert "raw secret" not in str((await repository.get(UUID(int=10))).health)

    asyncio.run(scenario())


def test_restart_honors_persisted_reconnect_deadline_before_contacting_client():
    """Ignoring retry_at after a restart would bypass the supervisor's bounded backoff policy."""
    async def scenario():
        repository, session = make_repository()
        clock = ManualClock()
        await repository.save(
            ConnectionRecord(
                account_id=UUID(int=10),
                session_ref=session,
                health=ConnectionHealth(
                    state="quarantine",
                    last_seen_at=clock.now(),
                    proxy_ip=None,
                    latency_ms=None,
                    error_code="connection_failed",
                ),
                retry_count=1,
                retry_at=clock.now() + timedelta(seconds=3),
            )
        )
        sleeper = AdvancingSleeper(clock)
        supervisor = make_supervisor(repository, ScriptedFactory([None]), clock, sleeper)

        assert (await supervisor.start(UUID(int=10))).state == "active"
        assert sleeper.delays == [3]

    asyncio.run(scenario())


def test_pause_cancels_a_retry_and_keeps_persisted_state_terminal():
    """Letting a cancelled retry write active after pause would resurrect an explicitly stopped account."""
    class BlockingSleeper:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def sleep(self, seconds: float) -> None:
            self.entered.set()
            await self.release.wait()

    async def scenario():
        repository, _ = make_repository()
        clock = ManualClock()
        sleeper = BlockingSleeper()
        supervisor = make_supervisor(repository, ScriptedFactory([RuntimeError("temporary")]), clock, sleeper)
        starting = asyncio.create_task(supervisor.start(UUID(int=10)))
        await sleeper.entered.wait()

        paused = await supervisor.pause(UUID(int=10))
        assert paused.state == "paused"
        assert (await supervisor.health(UUID(int=10))).state == "paused"
        assert (await starting).state == "paused"

    asyncio.run(scenario())


def test_auth_loss_and_block_are_terminal_safe_states():
    """Retrying authorization loss or blocked accounts would continue restricted activity."""
    async def scenario():
        for error, expected in ((AuthorizationLostError(), "reauth_required"), (AccountBlockedError(), "blocked")):
            repository, _ = make_repository()
            clock = ManualClock()
            factory = ScriptedFactory([error])
            supervisor = make_supervisor(repository, factory, clock, AdvancingSleeper(clock))
            health = await supervisor.start(UUID(int=10))

            assert health.state == expected
            assert factory.clients[0].disconnected is True
            assert (await supervisor.start(UUID(int=10))).state == expected

    asyncio.run(scenario())


def test_flood_wait_persists_limited_state_then_reconnects_after_retry_after():
    """Ignoring retry-after would retry too early; treating it as a raw exception would leak details."""
    async def scenario():
        repository, _ = make_repository()
        clock = ManualClock()
        sleeper = AdvancingSleeper(clock)
        supervisor = make_supervisor(repository, ScriptedFactory([FloodWaitError(4), None]), clock, sleeper)

        health = await supervisor.start(UUID(int=10))

        assert health.state == "active"
        assert sleeper.delays == [4]
        assert [record.health.state for record in repository.history] == ["limited", "active"]

    asyncio.run(scenario())


def test_stop_disconnects_active_client_and_archive_prevents_restart():
    """Not disconnecting on stop leaks a live client; ignoring archive would reconnect retired sessions."""
    async def scenario():
        repository, _ = make_repository()
        clock = ManualClock()
        factory = ScriptedFactory([None])
        supervisor = make_supervisor(repository, factory, clock, AdvancingSleeper(clock))
        await supervisor.start(UUID(int=10))

        assert (await supervisor.stop(UUID(int=10))).state == "paused"
        assert factory.clients[0].disconnected is True
        assert (await supervisor.archive(UUID(int=10))).state == "archived"
        assert (await supervisor.start(UUID(int=10))).state == "archived"

    asyncio.run(scenario())


def test_connection_record_rejects_a_session_owned_by_another_account():
    """Removing the account/session check could start one account with another account's credential."""
    with pytest.raises(ValueError, match="session reference account mismatch"):
        ConnectionRecord(
            account_id=UUID(int=10),
            session_ref=SessionRef(account_id=UUID(int=99), session_id=UUID(int=11), key_version=1),
            health=ConnectionHealth(
                state="quarantine",
                last_seen_at=None,
                proxy_ip=None,
                latency_ms=None,
                error_code=None,
            ),
        )


@pytest.mark.parametrize("failure_source", ["sleeper", "repository"])
def test_abnormal_monitor_exit_disconnects_and_releases_the_lease_for_takeover(failure_source):
    """An unhandled heartbeat failure must not leave a live client holding a split-brain lease."""

    class FailingMonitorSleeper:
        async def sleep(self, seconds: float) -> None:
            raise RuntimeError("MONITOR-SLEEPER-SENTINEL-SECRET")

    class RepositoryFailureAfterActivation(InMemoryConnectionRepository):
        def __init__(self, records, clock):
            super().__init__(records, clock=clock)
            self.renewals = 0

        async def renew_lease(self, record, owner_id, *, lease_seconds):
            self.renewals += 1
            if self.renewals == 2:
                raise RuntimeError("MONITOR-REPOSITORY-SENTINEL-SECRET")
            return await super().renew_lease(record, owner_id, lease_seconds=lease_seconds)

    async def scenario():
        clock = ManualClock()
        seed_repository, _ = make_repository(repository_clock=clock)
        seed = await seed_repository.get(UUID(int=10))
        assert seed is not None
        repository = (
            RepositoryFailureAfterActivation((seed,), clock)
            if failure_source == "repository"
            else InMemoryConnectionRepository((seed,), clock=clock)
        )
        first_factory = ScriptedFactory([None])
        first = make_supervisor(
            repository,
            first_factory,
            clock,
            AdvancingSleeper(clock),
            monitor_sleeper=(
                FailingMonitorSleeper() if failure_source == "sleeper" else AdvancingSleeper(clock)
            ),
            lease_duration_seconds=30,
        )

        assert (await first.start(UUID(int=10))).state == "active"
        for _ in range(10):
            if first_factory.clients[0].disconnected:
                break
            await asyncio.sleep(0)

        assert first_factory.clients[0].disconnected is True
        failed_closed = await repository.get(UUID(int=10))
        assert failed_closed is not None
        assert (failed_closed.health.state, failed_closed.health.error_code) == (
            "quarantine",
            "monitor_failed",
        )
        assert failed_closed.lease_owner_id is None

        takeover_factory = ScriptedFactory([None])
        takeover = make_supervisor(
            repository,
            takeover_factory,
            clock,
            AdvancingSleeper(clock),
        )
        assert (await takeover.start(UUID(int=10))).state == "active"
        assert len(takeover_factory.clients) == 1

    asyncio.run(scenario())
