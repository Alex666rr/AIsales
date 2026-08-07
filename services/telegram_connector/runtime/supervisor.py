"""A single-flight, persistence-first connection supervisor."""

import asyncio
from datetime import timedelta
from uuid import UUID

from telegram_connector.proxies import ProxyAssignmentRepository, ProxyAssignmentService, ProxyHealthChecker
from telegram_connector.runtime.connection import (
    AccountBlockedError,
    AuthorizationLostError,
    Clock,
    ConnectionHealth,
    ConnectionRecord,
    ConnectionRepository,
    FloodWaitError,
    Sleeper,
    TelegramClient,
    TelegramClientFactory,
)


_TERMINAL_STATES = frozenset({"paused", "reauth_required", "blocked", "archived"})


class ConnectionSupervisor:
    """Connect exactly one account at a time using authoritative persisted state.

    It deliberately never selects a second proxy after a failure.  Any proxy
    health failure blocks this account until a persisted assignment is changed
    by an operator.
    """

    def __init__(
        self,
        *,
        repository: ConnectionRepository,
        proxy_repository: ProxyAssignmentRepository,
        proxy_checker: ProxyHealthChecker,
        client_factory: TelegramClientFactory,
        clock: Clock,
        sleeper: Sleeper,
        max_retries: int = 5,
        max_backoff_seconds: float = 60.0,
        max_retry_after_seconds: int = 86_400,
    ) -> None:
        if max_retries < 1 or max_backoff_seconds <= 0 or max_retry_after_seconds < 1:
            raise ValueError("invalid reconnect bounds")
        self._repository = repository
        self._proxies = ProxyAssignmentService(proxy_repository, proxy_checker)
        self._proxy_repository = proxy_repository
        self._factory = client_factory
        self._clock = clock
        self._sleeper = sleeper
        self._max_retries = max_retries
        self._max_backoff_seconds = max_backoff_seconds
        self._max_retry_after_seconds = max_retry_after_seconds
        self._tasks: dict[UUID, asyncio.Task[ConnectionHealth]] = {}
        self._clients: dict[UUID, TelegramClient] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._generations: dict[UUID, int] = {}

    async def start(self, account_id: UUID) -> ConnectionHealth:
        """Start (or join) one connection loop; terminal persisted states remain terminal."""
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            existing = self._tasks.get(account_id)
            if existing is not None and not existing.done():
                task = existing
            elif account_id in self._clients:
                return await self.health(account_id)
            else:
                generation = self._generations.get(account_id, 0)
                task = asyncio.create_task(self._run(account_id, generation))
                self._tasks[account_id] = task
        return await asyncio.shield(task)

    async def stop(self, account_id: UUID) -> ConnectionHealth:
        """Pause an account and cancel every pending reconnect before it can revive."""
        return await self.pause(account_id)

    async def pause(self, account_id: UUID) -> ConnectionHealth:
        """Persist a terminal pause before cancelling the loop and closing the client."""
        return await self._terminal_transition(account_id, "paused")

    async def archive(self, account_id: UUID) -> ConnectionHealth:
        """Persist archive state and prevent all future starts for this session record."""
        return await self._terminal_transition(account_id, "archived")

    async def health(self, account_id: UUID) -> ConnectionHealth:
        """Read health from the repository so a restart sees the same truth."""
        record = await self._repository.get(account_id)
        if record is None:
            raise KeyError("connection record was not found")
        return record.health

    async def _terminal_transition(self, account_id: UUID, state: str) -> ConnectionHealth:
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            record = await self._required_record(account_id)
            self._generations[account_id] = self._generations.get(account_id, 0) + 1
            health = self._state(record, state=state, error_code=None)
            await self._repository.save(record.model_copy(update={"health": health, "retry_at": None}))
            task = self._tasks.get(account_id)
            current = asyncio.current_task()
            if task is not None and task is not current and not task.done():
                task.cancel()
            client = self._clients.pop(account_id, None)
        if client is not None:
            await self._safe_disconnect(client)
        if task is not None and task is not asyncio.current_task():
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._proxy_repository.release_assignment(account_id)
        return await self.health(account_id)

    async def _run(self, account_id: UUID, generation: int) -> ConnectionHealth:
        try:
            record = await self._required_record(account_id)
            if record.health.state in _TERMINAL_STATES:
                return record.health
            while self._current(account_id, generation):
                record = await self._required_record(account_id)
                if record.health.state in _TERMINAL_STATES:
                    return record.health
                if record.retry_at is not None and record.retry_at > self._clock.now():
                    await self._sleeper.sleep((record.retry_at - self._clock.now()).total_seconds())
                    if not self._current(account_id, generation):
                        return await self.health(account_id)
                    continue
                lease = await self._proxies.acquire(account_id)
                if not self._current(account_id, generation):
                    return await self.health(account_id)
                if lease is None or not lease.health.available:
                    return await self._save_state(
                        record,
                        state="blocked",
                        error_code="proxy_unavailable",
                        retry_at=None,
                    )
                client: TelegramClient | None = None
                try:
                    client = await self._factory.create(record.session_ref, lease.proxy)
                    self._clients[account_id] = client
                    await client.connect()
                    if not await client.is_authorized():
                        raise AuthorizationLostError()
                    if not self._current(account_id, generation):
                        return await self.health(account_id)
                    return await self._save_state(
                        record,
                        state="active",
                        error_code=None,
                        proxy_ip=lease.health.ip_address,
                        latency_ms=lease.health.latency_ms,
                        retry_count=0,
                        retry_at=None,
                    )
                except AuthorizationLostError:
                    health = await self._save_state(
                        record, state="reauth_required", error_code="authorization_lost", retry_at=None
                    )
                    await self._drop_client(account_id, client)
                    return health
                except AccountBlockedError:
                    health = await self._save_state(record, state="blocked", error_code="account_blocked", retry_at=None)
                    await self._drop_client(account_id, client)
                    return health
                except FloodWaitError as error:
                    retries = record.retry_count + 1
                    retry_at = self._clock.now() + timedelta(seconds=error.retry_after_seconds)
                    limited = await self._save_state(
                        record,
                        state="limited",
                        error_code="rate_limited",
                        retry_count=retries,
                        retry_at=retry_at,
                    )
                    await self._drop_client(account_id, client)
                    if retries >= self._max_retries or error.retry_after_seconds > self._max_retry_after_seconds:
                        return limited
                    await self._sleeper.sleep(error.retry_after_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    retries = record.retry_count + 1
                    delay = min(float(2 ** (retries - 1)), self._max_backoff_seconds)
                    quarantined = await self._save_state(
                        record,
                        state="quarantine",
                        error_code="connection_failed",
                        retry_count=retries,
                        retry_at=self._clock.now() + timedelta(seconds=delay),
                    )
                    await self._drop_client(account_id, client)
                    if retries >= self._max_retries:
                        return quarantined
                    await self._sleeper.sleep(delay)
                finally:
                    if client is not None and self._clients.get(account_id) is not client:
                        await self._safe_disconnect(client)
            return await self.health(account_id)
        except asyncio.CancelledError:
            return await self.health(account_id)
        finally:
            task = self._tasks.get(account_id)
            if task is asyncio.current_task():
                self._tasks.pop(account_id, None)

    async def _drop_client(self, account_id: UUID, client: TelegramClient | None) -> None:
        if client is not None and self._clients.get(account_id) is client:
            self._clients.pop(account_id, None)
            await self._safe_disconnect(client)

    async def _save_state(
        self,
        record: ConnectionRecord,
        *,
        state: str,
        error_code: str | None,
        retry_at,
        proxy_ip: str | None = None,
        latency_ms: int | None = None,
        retry_count: int | None = None,
    ) -> ConnectionHealth:
        health = self._state(
            record,
            state=state,
            error_code=error_code,
            proxy_ip=proxy_ip,
            latency_ms=latency_ms,
        )
        updated = record.model_copy(
            update={
                "health": health,
                "retry_count": record.retry_count if retry_count is None else retry_count,
                "retry_at": retry_at,
            }
        )
        await self._repository.save(updated)
        return health

    def _state(
        self,
        record: ConnectionRecord,
        *,
        state: str,
        error_code: str | None,
        proxy_ip: str | None = None,
        latency_ms: int | None = None,
    ) -> ConnectionHealth:
        return ConnectionHealth(
            state=state,
            last_seen_at=self._clock.now(),
            proxy_ip=proxy_ip,
            latency_ms=latency_ms,
            error_code=error_code,
        )

    async def _required_record(self, account_id: UUID) -> ConnectionRecord:
        record = await self._repository.get(account_id)
        if record is None:
            raise KeyError("connection record was not found")
        return record

    def _current(self, account_id: UUID, generation: int) -> bool:
        return self._generations.get(account_id, 0) == generation

    @staticmethod
    async def _safe_disconnect(client: TelegramClient) -> None:
        try:
            await client.disconnect()
        except Exception:
            pass
