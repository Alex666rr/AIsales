"""A single-flight, persistence-first connection supervisor."""

import asyncio
import math
from datetime import timedelta
from uuid import UUID, uuid4

from telegram_connector.proxies import ProxyAssignmentRepository, ProxyAssignmentService, ProxyHealthChecker, ProxyLease
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
        lease_duration_seconds: float = 60.0,
        heartbeat_interval_seconds: float = 15.0,
        monitor_sleeper: Sleeper | None = None,
    ) -> None:
        if max_retries < 1 or max_backoff_seconds <= 0 or max_retry_after_seconds < 1 or not all(math.isfinite(value) and value > 0 for value in (lease_duration_seconds, heartbeat_interval_seconds)) or heartbeat_interval_seconds * 3 > lease_duration_seconds:
            raise ValueError("invalid reconnect bounds")
        self._repository = repository
        self._proxies = ProxyAssignmentService(proxy_repository, proxy_checker)
        self._factory = client_factory
        self._clock = clock
        self._sleeper = sleeper
        self._max_retries = max_retries
        self._max_backoff_seconds = max_backoff_seconds
        self._max_retry_after_seconds = max_retry_after_seconds
        self._lease_duration_seconds = lease_duration_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._monitor_sleeper = monitor_sleeper or sleeper
        self._tasks: dict[UUID, asyncio.Task[ConnectionHealth]] = {}
        self._clients: dict[UUID, TelegramClient] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._monitor_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._owner_id = uuid4()

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
                claimed = await self._repository.try_claim(account_id, self._owner_id, lease_seconds=self._lease_duration_seconds)
                if claimed is None:
                    return await self.health(account_id)
                if claimed.health.state in _TERMINAL_STATES:
                    return claimed.health
                task = asyncio.create_task(self._run(account_id, claimed))
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
            record = await self._repository.force_terminal(account_id, state, self._clock.now())
            task = self._tasks.get(account_id)
            current = asyncio.current_task()
            if task is not None and task is not current and not task.done():
                task.cancel()
            client = self._clients.get(account_id)
        cleanup = asyncio.create_task(self._terminal_cleanup(account_id, client))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await asyncio.shield(cleanup)
            raise
        return record.health

    async def _run(self, account_id: UUID, record: ConnectionRecord) -> ConnectionHealth:
        try:
            while True:
                renewed = await self._repository.renew_lease(record, self._owner_id, lease_seconds=self._lease_duration_seconds)
                if renewed is None:
                    return await self.health(account_id)
                record = renewed
                if record.health.state in _TERMINAL_STATES:
                    return record.health
                if record.retry_at is not None and record.retry_at > self._clock.now():
                    await self._sleeper.sleep((record.retry_at - self._clock.now()).total_seconds())
                    if not await self._owns_claim(record):
                        return await self.health(account_id)
                    continue
                lease = await self._proxies.acquire(account_id)
                if not await self._owns_claim(record):
                    await self._cleanup_failed_reservation(account_id, lease)
                    return await self.health(account_id)
                if lease is None or not lease.health.available:
                    await self._cleanup_failed_reservation(account_id, lease)
                    saved = await self._save_state(
                        record,
                        state="blocked",
                        error_code="proxy_unavailable",
                        retry_at=None,
                        release_lease=True,
                    )
                    return saved.health if saved is not None else await self.health(account_id)
                client: TelegramClient | None = None
                try:
                    client = await self._factory.create(record.session_ref, lease.proxy)
                    if not await self._owns_claim(record):
                        await self._cleanup_failed_reservation(account_id, lease)
                        await self._safe_disconnect(client)
                        return await self.health(account_id)
                    self._clients[account_id] = client
                    await client.connect()
                    if not await self._owns_claim(record):
                        await self._cleanup_failed_reservation(account_id, lease)
                        await self._drop_client(account_id, client)
                        return await self.health(account_id)
                    if not await client.is_authorized():
                        raise AuthorizationLostError()
                    if not await self._owns_claim(record):
                        await self._cleanup_failed_reservation(account_id, lease)
                        await self._drop_client(account_id, client)
                        return await self.health(account_id)
                    saved = await self._save_state(
                        record,
                        state="active",
                        error_code=None,
                        proxy_ip=lease.health.ip_address,
                        latency_ms=lease.health.latency_ms,
                        retry_count=0,
                        retry_at=None,
                        release_lease=False,
                    )
                    if saved is None:
                        await self._drop_client(account_id, client)
                        return await self.health(account_id)
                    self._monitor_tasks[account_id] = asyncio.create_task(self._monitor(account_id, saved, client))
                    return saved.health
                except AuthorizationLostError:
                    await self._cleanup_failed_reservation(account_id, lease)
                    saved = await self._save_state(
                        record, state="reauth_required", error_code="authorization_lost", retry_at=None, release_lease=True
                    )
                    await self._drop_client(account_id, client)
                    return saved.health if saved is not None else await self.health(account_id)
                except AccountBlockedError:
                    await self._cleanup_failed_reservation(account_id, lease)
                    saved = await self._save_state(
                        record, state="blocked", error_code="account_blocked", retry_at=None, release_lease=True
                    )
                    await self._drop_client(account_id, client)
                    return saved.health if saved is not None else await self.health(account_id)
                except FloodWaitError as error:
                    retries = record.retry_count + 1
                    retry_at = self._clock.now() + timedelta(seconds=error.retry_after_seconds)
                    exhausted = retries >= self._max_retries or error.retry_after_seconds > self._max_retry_after_seconds
                    await self._cleanup_failed_reservation(account_id, lease)
                    saved = await self._save_state(
                        record, state="blocked" if exhausted else "limited",
                        error_code="retry_exhausted" if exhausted else "rate_limited",
                        retry_count=retries, retry_at=None if exhausted else retry_at, release_lease=exhausted,
                    )
                    await self._drop_client(account_id, client)
                    if saved is None:
                        return await self.health(account_id)
                    if exhausted:
                        return saved.health
                    record = saved
                    await self._sleeper.sleep(error.retry_after_seconds)
                    if not await self._owns_claim(record):
                        return await self.health(account_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    retries = record.retry_count + 1
                    delay = min(float(2 ** (retries - 1)), self._max_backoff_seconds)
                    exhausted = retries >= self._max_retries
                    await self._cleanup_failed_reservation(account_id, lease)
                    saved = await self._save_state(
                        record,
                        state="blocked" if exhausted else "quarantine",
                        error_code="retry_exhausted" if exhausted else "connection_failed",
                        retry_count=retries,
                        retry_at=None if exhausted else self._clock.now() + timedelta(seconds=delay),
                        release_lease=exhausted,
                    )
                    await self._drop_client(account_id, client)
                    if saved is None:
                        return await self.health(account_id)
                    if exhausted:
                        return saved.health
                    record = saved
                    await self._sleeper.sleep(delay)
                    if not await self._owns_claim(record):
                        return await self.health(account_id)
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
        release_lease: bool = False,
    ) -> ConnectionRecord | None:
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
        return await self._repository.save_claimed(updated, self._owner_id, release_lease=release_lease)

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

    async def _owns_claim(self, record: ConnectionRecord) -> bool:
        current = await self._repository.get(record.account_id)
        return current is not None and current.version == record.version and current.lease_owner_id == self._owner_id and current.fence_token == record.fence_token and current.lease_expires_at is not None and current.lease_expires_at > self._clock.now()

    async def _terminal_cleanup(self, account_id: UUID, client: TelegramClient | None) -> None:
        try:
            monitor = self._monitor_tasks.pop(account_id, None)
            if monitor is not None:
                monitor.cancel()
                try:
                    await monitor
                except asyncio.CancelledError:
                    pass
            if client is not None:
                await self._safe_disconnect(client)
                self._clients.pop(account_id, None)
        finally:
            await self._proxies.release_terminal(account_id)

    async def _cleanup_failed_reservation(self, account_id: UUID, lease: ProxyLease | None) -> None:
        if lease is not None:
            await self._proxies.release_failed(account_id, lease)

    async def _monitor(self, account_id: UUID, record: ConnectionRecord, client: TelegramClient) -> None:
        try:
            while True:
                await self._monitor_sleeper.sleep(self._heartbeat_interval_seconds)
                renewed = await self._repository.renew_lease(record, self._owner_id, lease_seconds=self._lease_duration_seconds)
                if renewed is None:
                    await self._drop_client(account_id, client)
                    return
                record = renewed
        except asyncio.CancelledError:
            return
        finally:
            if self._monitor_tasks.get(account_id) is asyncio.current_task():
                self._monitor_tasks.pop(account_id, None)

    @staticmethod
    async def _safe_disconnect(client: TelegramClient) -> None:
        try:
            await client.disconnect()
        except Exception:
            pass
