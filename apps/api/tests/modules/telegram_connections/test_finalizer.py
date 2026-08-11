from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.modules.telegram_connections.finalizer import ConnectionFinalizer, ConnectionUnavailable
from telegram_connector.runtime.connection import ConnectionHealth, InMemoryConnectionRepository
from telegram_connector.session_store import EncryptedSessionStore


class FakeAccountProvisioner:
    def __init__(self, account_id: UUID) -> None:
        self.account_id = account_id
        self.calls: list[tuple[UUID, int]] = []

    async def provision(self, organization_id: UUID, telegram_user_id: int) -> UUID:
        self.calls.append((organization_id, telegram_user_id))
        return self.account_id


class FakeSupervisor:
    def __init__(self) -> None:
        self.started: list[UUID] = []

    async def start(self, account_id: UUID) -> ConnectionHealth:
        self.started.append(account_id)
        return ConnectionHealth(
            state="quarantine", last_seen_at=None, proxy_ip=None, latency_ms=None, error_code=None
        )


class FailingConnectionRepository:
    async def save(self, record) -> None:
        raise RuntimeError("database down")


def test_finalizer_encrypts_session_before_persisting_connection_and_starts_supervisor() -> None:
    account_id, organization_id = uuid4(), uuid4()
    store = EncryptedSessionStore.test_store({1: b"k" * 32}, active_key_version=1)
    connections = InMemoryConnectionRepository()
    supervisor = FakeSupervisor()
    finalizer = ConnectionFinalizer(
        accounts=FakeAccountProvisioner(account_id),
        sessions=store,
        connections=connections,
        supervisor=supervisor,
        now=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )

    result = asyncio.run(
        finalizer.finalize(
            organization_id=organization_id,
            telegram_user_id=123456,
            session_payload=b"TELETHON_STRING_SESSION\x00\x01synthetic-session",
        )
    )

    assert result.account_id == account_id
    assert result.telegram_user_id == 123456
    assert supervisor.started == [account_id]
    record = asyncio.run(connections.get(account_id))
    assert record is not None
    ciphertext = store.persisted_records()[0].ciphertext
    assert b"synthetic-session" not in ciphertext


def test_finalizer_never_starts_supervisor_when_connection_persistence_fails() -> None:
    supervisor = FakeSupervisor()
    finalizer = ConnectionFinalizer(
        accounts=FakeAccountProvisioner(uuid4()),
        sessions=EncryptedSessionStore.test_store({1: b"k" * 32}, active_key_version=1),
        connections=FailingConnectionRepository(),
        supervisor=supervisor,
        now=lambda: datetime(2026, 8, 11, tzinfo=UTC),
    )

    with pytest.raises(ConnectionUnavailable, match="^telegram connection unavailable$"):
        asyncio.run(
            finalizer.finalize(
                organization_id=uuid4(),
                telegram_user_id=123456,
                session_payload=b"TELETHON_STRING_SESSION\x00\x01synthetic-session",
            )
        )

    assert supervisor.started == []


def test_telegram_identity_migration_creates_unique_numeric_identity() -> None:
    from importlib import import_module
    from io import StringIO

    module = import_module("apps.api.app.db.migrations.versions.0004_telegram_connection_identity")
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://migration-test.invalid/prototype",
        opts={"as_sql": True, "literal_binds": True, "output_buffer": output},
    )
    module.op = Operations(context)
    module.upgrade()

    rendered = output.getvalue()
    assert "telegram_user_id BIGINT" in rendered
    assert "uq_telegram_accounts_telegram_user_id" in rendered
