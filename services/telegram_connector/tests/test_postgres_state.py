"""Durable source-of-truth contracts for Stage 0 Telegram state."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import telegram_connector.persistence as persistence
from telegram_connector.proxies import ProxyConfig
from telegram_connector.runtime.connection import ConnectionHealth, ConnectionRecord
from telegram_connector.session_store import EncryptedSessionStore, SessionRef


ACCOUNT_ONE = UUID(int=101)
ACCOUNT_TWO = UUID(int=102)
ORGANIZATION = UUID(int=201)
SESSION = UUID(int=301)
PROXY = UUID(int=401)


def durable_sessions(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'telegram-state.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    event.listen(
        engine,
        "connect",
        lambda dbapi_connection, _record: dbapi_connection.execute("PRAGMA foreign_keys=ON"),
    )
    persistence.create_gateway_schema(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def repository_type(name: str):
    assert hasattr(persistence, name), f"missing durable repository: {name}"
    return getattr(persistence, name)


def test_encrypted_session_ciphertext_survives_repository_restart_without_plaintext(tmp_path):
    """Replacing PostgreSQL storage with process memory would lose a session on worker restart."""
    engine, sessions = durable_sessions(tmp_path)
    try:
        account_repository = repository_type("SqlAlchemyTelegramAccountRepository")(sessions)
        account_repository.put(ACCOUNT_ONE, ORGANIZATION)
        repository_class = repository_type("SqlAlchemyCiphertextSessionRepository")
        first = EncryptedSessionStore(
            {1: b"k" * 32},
            active_key_version=1,
            repository=repository_class(sessions),
        )
        reference = first.put(ACCOUNT_ONE, b"sensitive-session-plaintext")

        restarted = EncryptedSessionStore(
            {1: b"k" * 32},
            active_key_version=1,
            repository=repository_class(sessions),
        )
        assert restarted.get(reference) == b"sensitive-session-plaintext"
        with sessions() as session:
            row = session.execute(select(persistence.telegram_session_ciphertexts)).mappings().one()
        assert b"sensitive-session-plaintext" not in row["ciphertext"]
        assert set(row) == {"session_id", "account_id", "key_version", "ciphertext", "created_at"}
    finally:
        engine.dispose()


def test_connection_state_is_restart_durable_and_fenced_across_repository_instances(tmp_path):
    """Two workers must share one CAS/fencing record instead of creating two live clients."""

    async def scenario():
        engine, sessions = durable_sessions(tmp_path)
        try:
            repository_type("SqlAlchemyTelegramAccountRepository")(sessions).put(ACCOUNT_ONE, ORGANIZATION)
            session_repository = repository_type("SqlAlchemyCiphertextSessionRepository")(sessions)
            reference = EncryptedSessionStore(
                {1: b"k" * 32}, active_key_version=1, repository=session_repository
            ).put(ACCOUNT_ONE, b"session")
            repository_class = repository_type("SqlAlchemyConnectionRepository")
            first = repository_class(sessions)
            restarted = repository_class(sessions)
            await first.save(
                ConnectionRecord(
                    account_id=ACCOUNT_ONE,
                    session_ref=reference,
                    health=ConnectionHealth(
                        state="quarantine",
                        last_seen_at=None,
                        proxy_ip=None,
                        latency_ms=None,
                        error_code=None,
                    ),
                    retry_count=2,
                    retry_at=datetime(2026, 8, 12, tzinfo=UTC),
                )
            )

            persisted = await restarted.get(ACCOUNT_ONE)
            assert persisted is not None
            assert (persisted.retry_count, persisted.session_ref) == (2, reference)
            owner_one = UUID(int=501)
            owner_two = UUID(int=502)
            claim = await first.try_claim(ACCOUNT_ONE, owner_one, lease_seconds=30)
            assert claim is not None
            assert await restarted.try_claim(ACCOUNT_ONE, owner_two, lease_seconds=30) is None
            stale = claim.model_copy(
                update={"health": claim.health.model_copy(update={"state": "active"})}
            )
            assert await restarted.save_claimed(stale, owner_two, release_lease=False) is None
        finally:
            engine.dispose()

    asyncio.run(scenario())


def test_proxy_capacity_override_and_credentials_are_atomic_and_restart_durable(tmp_path):
    """Independent workers must not overbook capacity or persist credential plaintext."""

    async def scenario():
        engine, sessions = durable_sessions(tmp_path)
        try:
            accounts = repository_type("SqlAlchemyTelegramAccountRepository")(sessions)
            accounts.put(ACCOUNT_ONE, ORGANIZATION)
            accounts.put(ACCOUNT_TWO, ORGANIZATION)
            cipher = repository_type("ProxyCredentialCipher")({7: b"p" * 32}, active_key_version=7)
            repository_class = repository_type("SqlAlchemyProxyAssignmentRepository")
            first = repository_class(sessions, credential_cipher=cipher)
            restarted = repository_class(sessions, credential_cipher=cipher)
            await first.put_proxy(
                ProxyConfig(
                    proxy_id=PROXY,
                    url="socks5://proxy-user:proxy-password@edge.example:1080",
                    capacity=1,
                ),
                default=True,
            )

            first_reservation = await first.reserve_assignment(ACCOUNT_ONE)
            assert first_reservation is not None
            assert await restarted.reserve_assignment(ACCOUNT_TWO) is None
            await restarted.set_account_override(ACCOUNT_TWO, PROXY)
            assert await restarted.reserve_assignment(ACCOUNT_TWO) is None
            await first.release_terminal_assignment(ACCOUNT_ONE)
            second_reservation = await restarted.reserve_assignment(ACCOUNT_TWO)
            assert second_reservation is not None
            assert second_reservation.account_override is True
            assert second_reservation.proxy.client_url == (
                "socks5://proxy-user:proxy-password@edge.example:1080"
            )

            with sessions() as session:
                row = session.execute(select(persistence.telegram_proxies)).mappings().one()
            persisted_values = repr(dict(row))
            assert "proxy-user" not in persisted_values
            assert "proxy-password" not in persisted_values
            assert row["endpoint"] == "socks5://edge.example:1080"
        finally:
            engine.dispose()

    asyncio.run(scenario())


def test_authoritative_account_organization_lookup_survives_restart(tmp_path):
    """Policy context issuance must use persisted ownership rather than caller-supplied organization IDs."""
    engine, sessions = durable_sessions(tmp_path)
    try:
        repository_class = repository_type("SqlAlchemyTelegramAccountRepository")
        repository_class(sessions).put(ACCOUNT_ONE, ORGANIZATION)

        assert repository_class(sessions).organization_for(ACCOUNT_ONE) == ORGANIZATION
        assert repository_class(sessions).organization_for(ACCOUNT_TWO) is None
    finally:
        engine.dispose()


def test_database_constraint_rejects_a_connection_with_the_wrong_session_key_version(tmp_path):
    """Repository validation alone cannot protect against a direct mismatched runtime insert."""
    engine, sessions = durable_sessions(tmp_path)
    try:
        repository_type("SqlAlchemyTelegramAccountRepository")(sessions).put(ACCOUNT_ONE, ORGANIZATION)
        reference = EncryptedSessionStore(
            {1: b"k" * 32},
            active_key_version=1,
            repository=repository_type("SqlAlchemyCiphertextSessionRepository")(sessions),
        ).put(ACCOUNT_ONE, b"session")

        with pytest.raises(IntegrityError):
            with sessions.begin() as session:
                session.execute(
                    insert(persistence.telegram_connections).values(
                        account_id=ACCOUNT_ONE,
                        session_id=reference.session_id,
                        key_version=99,
                        state="quarantine",
                    )
                )
    finally:
        engine.dispose()


def test_proxy_mutations_lock_capacity_and_account_serialization_targets(tmp_path, monkeypatch):
    """Capacity changes and missing override rows must serialize with assignment decisions."""

    async def scenario():
        engine, sessions = durable_sessions(tmp_path)
        try:
            repository_type("SqlAlchemyTelegramAccountRepository")(sessions).put(
                ACCOUNT_ONE, ORGANIZATION
            )
            cipher = repository_type("ProxyCredentialCipher")(
                {1: b"p" * 32}, active_key_version=1
            )
            repository = repository_type("SqlAlchemyProxyAssignmentRepository")(
                sessions, credential_cipher=cipher
            )
            await repository.put_proxy(
                ProxyConfig(proxy_id=PROXY, url="socks5://edge.example:1080", capacity=2),
                default=True,
            )

            locked_accounts: list[UUID] = []
            locked_proxies: list[UUID] = []

            def lock_account(session, account_id):
                locked_accounts.append(account_id)
                return session.execute(
                    select(persistence.telegram_accounts.c.account_id).where(
                        persistence.telegram_accounts.c.account_id == account_id
                    )
                ).scalar_one_or_none()

            def lock_proxy(session, proxy_id):
                locked_proxies.append(proxy_id)
                return session.execute(
                    select(persistence.telegram_proxies).where(
                        persistence.telegram_proxies.c.proxy_id == proxy_id
                    )
                ).mappings().one_or_none()

            monkeypatch.setattr(repository, "_lock_account", lock_account, raising=False)
            monkeypatch.setattr(repository, "_locked_proxy_row", lock_proxy, raising=False)

            await repository.put_proxy(
                ProxyConfig(proxy_id=PROXY, url="socks5://edge.example:1080", capacity=1),
                default=True,
            )
            reservation = await repository.reserve_assignment(ACCOUNT_ONE)
            assert reservation is not None
            await repository.release_terminal_assignment(ACCOUNT_ONE)
            await repository.set_account_override(ACCOUNT_ONE, PROXY)

            assert locked_proxies == [PROXY]
            assert locked_accounts == [ACCOUNT_ONE, ACCOUNT_ONE, ACCOUNT_ONE]
        finally:
            engine.dispose()

    asyncio.run(scenario())
