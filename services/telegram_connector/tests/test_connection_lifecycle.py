"""Lifecycle contracts for injected Telegram clients and persisted connection state."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

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


def make_repository(state: str = "quarantine") -> tuple[InMemoryConnectionRepository, SessionRef]:
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
            )
        ),
        session,
    )


def make_supervisor(repository, factory, clock, sleeper) -> ConnectionSupervisor:
    proxy_repository = InMemoryProxyAssignmentRepository(
        proxies=(ProxyConfig(proxy_id=UUID(int=1), url="socks5://edge.example:1080", capacity=5),),
        default_proxy_id=UUID(int=1),
    )
    return ConnectionSupervisor(
        repository=repository,
        proxy_repository=proxy_repository,
        proxy_checker=AvailableProxyChecker(),
        client_factory=factory,
        clock=clock,
        sleeper=sleeper,
        max_retries=2,
        max_backoff_seconds=10,
    )


def test_start_persists_active_health_and_restart_reconnects_from_persisted_state():
    """Using only in-memory health would lose the connection contract after a process restart."""
    async def scenario():
        repository, _ = make_repository()
        clock = ManualClock()
        first_factory = ScriptedFactory([None])
        first = make_supervisor(repository, first_factory, clock, AdvancingSleeper(clock))

        assert (await first.start(UUID(int=10))).state == "active"
        assert (await repository.get(UUID(int=10))).health.state == "active"

        restarted_factory = ScriptedFactory([None])
        restarted = make_supervisor(repository, restarted_factory, clock, AdvancingSleeper(clock))
        assert (await restarted.start(UUID(int=10))).state == "active"
        assert len(restarted_factory.clients) == 1

    asyncio.run(scenario())


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
