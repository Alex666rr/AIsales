from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.modules.policy.models import PlatformOwnerPrincipal
from app.modules.shared.commands import TenantContext
from app.modules.telegram_connections.models import AttemptStatus, ConnectionMethod, TdataConnectionView
from app.modules.telegram_connections.service import (
    ConnectionAttemptService,
    ConnectionStatusService,
    WorkspaceAccountDirectoryService,
    WorkspaceConnectionAttemptService,
)
from telegram_connector.adapters.phone import AuthStep
from telegram_connector.runtime.connection import ConnectionHealth, ConnectionRecord
from telegram_connector.session_store import SessionRef


class FakePhoneAdapter:
    def __init__(self) -> None:
        self.owner_id: UUID | None = None
        self.challenge_id = uuid4()
        self.expires_at = datetime.now(UTC) + timedelta(minutes=5)

    async def start(self, phone: str, owner_id: UUID) -> AuthStep:
        self.owner_id = owner_id
        return AuthStep(
            state="code_sent",
            challenge_id=self.challenge_id,
            expires_at=self.expires_at,
            safe_message="Authorization code was requested.",
        )

    async def submit_code(self, challenge_id: UUID, owner_id: UUID, code: str) -> AuthStep:
        state = "needs_2fa" if owner_id == self.owner_id and code == "needs-2fa" else "failed"
        return AuthStep(
            state=state,
            challenge_id=challenge_id,
            expires_at=self.expires_at,
            safe_message="safe",
        )

    async def submit_password(self, challenge_id: UUID, owner_id: UUID, password: str) -> AuthStep:
        state = "authorized" if owner_id == self.owner_id and password == "correct" else "failed"
        return AuthStep(
            state=state,
            challenge_id=challenge_id,
            expires_at=self.expires_at,
            safe_message="safe",
        )

    async def consume_authorized_session(self, challenge_id: UUID, owner_id: UUID) -> tuple[int, bytes]:
        return 123456, b"TELETHON_STRING_SESSION\x00\x01phone-session"


class FakeQrAdapter:
    def __init__(self) -> None:
        self.challenge_id = uuid4()
        self.expires_at = datetime.now(UTC) + timedelta(minutes=2)

    async def start_background(self, owner_id: UUID) -> tuple[AuthStep, str]:
        return AuthStep(
            state="code_sent",
            challenge_id=self.challenge_id,
            expires_at=self.expires_at,
            safe_message="safe",
        ), "tg://login?token=QR-SENTINEL"

    async def status(self, challenge_id: UUID, owner_id: UUID) -> AuthStep:
        return AuthStep(
            state="authorized",
            challenge_id=challenge_id,
            expires_at=self.expires_at,
            safe_message="safe",
        )

    async def consume_authorized_session(self, challenge_id: UUID, owner_id: UUID) -> tuple[int, bytes]:
        return 123456, b"TELETHON_STRING_SESSION\x00\x01qr-session"


class FakeFinalizer:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int, bytes]] = []
        self.account_id = uuid4()

    async def finalize(self, *, organization_id: UUID, telegram_user_id: int, session_payload: bytes) -> TdataConnectionView:
        self.calls.append((organization_id, telegram_user_id, session_payload))
        return TdataConnectionView(
            account_id=self.account_id, telegram_user_id=telegram_user_id, state="quarantine"
        )


class FakeAccounts:
    def __init__(self, owner_id: UUID) -> None:
        self.owner_id = owner_id

    async def organization_for_async(self, account_id: UUID) -> UUID | None:
        return self.owner_id if account_id.int == 1 else None


class FakeConnections:
    async def get(self, account_id: UUID) -> ConnectionRecord | None:
        if account_id.int != 1:
            return None
        return ConnectionRecord(
            account_id=account_id,
            session_ref=SessionRef(account_id=account_id, session_id=uuid4(), key_version=1),
            health=ConnectionHealth(
                state="quarantine", last_seen_at=datetime.now(UTC), proxy_ip=None,
                latency_ms=None, error_code=None,
            ),
        )


class FakeDirectoryAccounts:
    def __init__(self, account_ids: tuple[UUID, ...]) -> None:
        self.account_ids = account_ids
        self.requested_organization_id: UUID | None = None

    async def list_account_ids_by_organization_async(self, organization_id: UUID) -> tuple[UUID, ...]:
        self.requested_organization_id = organization_id
        return self.account_ids


class FakeDirectoryConnections:
    def __init__(self, records: dict[UUID, ConnectionRecord]) -> None:
        self.records = records

    async def get(self, account_id: UUID) -> ConnectionRecord | None:
        return self.records.get(account_id)


def test_phone_attempt_maps_code_and_2fa_without_exposing_phone_or_code() -> None:
    phone = FakePhoneAdapter()
    service = ConnectionAttemptService(phone=phone, qr=FakeQrAdapter())
    owner = PlatformOwnerPrincipal(principal_id=uuid4())

    started = asyncio.run(service.start_phone(owner, "+12025550123"))
    password_required = asyncio.run(service.submit_code(owner, started.attempt_id, "needs-2fa"))

    assert started.method is ConnectionMethod.PHONE
    assert started.status is AttemptStatus.CODE_REQUESTED
    assert password_required.status is AttemptStatus.PASSWORD_REQUIRED
    assert "+12025550123" not in repr(started)
    assert "needs-2fa" not in repr(password_required)


def test_other_owner_cannot_complete_phone_attempt() -> None:
    phone = FakePhoneAdapter()
    service = ConnectionAttemptService(phone=phone, qr=FakeQrAdapter())
    started = asyncio.run(service.start_phone(PlatformOwnerPrincipal(principal_id=uuid4()), "+12025550123"))

    result = asyncio.run(service.submit_code(PlatformOwnerPrincipal(principal_id=uuid4()), started.attempt_id, "12345"))

    assert result.status is AttemptStatus.FAILED


def test_qr_attempt_returns_only_the_required_short_lived_payload() -> None:
    service = ConnectionAttemptService(phone=FakePhoneAdapter(), qr=FakeQrAdapter())

    started = asyncio.run(service.start_qr(PlatformOwnerPrincipal(principal_id=uuid4())))

    assert started.method is ConnectionMethod.QR
    assert started.status is AttemptStatus.PENDING
    assert started.qr_url == "tg://login?token=QR-SENTINEL"
    assert "QR-SENTINEL" not in repr(started)


def test_authorized_phone_and_qr_attempts_finalize_their_one_time_sessions() -> None:
    async def scenario() -> None:
        owner = PlatformOwnerPrincipal(principal_id=uuid4())
        phone, qr, finalizer = FakePhoneAdapter(), FakeQrAdapter(), FakeFinalizer()
        service = ConnectionAttemptService(phone=phone, qr=qr, finalizer=finalizer)

        phone_started = await service.start_phone(owner, "+12025550123")
        await service.submit_code(owner, phone_started.attempt_id, "needs-2fa")
        phone_complete = await service.submit_password(owner, phone_started.attempt_id, "correct")
        qr_started = await service.start_qr(owner)
        qr_complete = await service.qr_status(owner, qr_started.attempt_id)

        assert phone_complete.status is AttemptStatus.AUTHORIZED
        assert qr_complete.status is AttemptStatus.AUTHORIZED
        assert phone_complete.account_id == finalizer.account_id
        assert qr_complete.account_id == finalizer.account_id
        assert finalizer.calls == [
            (owner.principal_id, 123456, b"TELETHON_STRING_SESSION\x00\x01phone-session"),
            (owner.principal_id, 123456, b"TELETHON_STRING_SESSION\x00\x01qr-session"),
        ]

    asyncio.run(scenario())


def test_connection_status_is_visible_only_to_the_owner_that_provisioned_the_account() -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        service = ConnectionStatusService(accounts=FakeAccounts(owner_id), connections=FakeConnections())
        account_id = UUID(int=1)

        result = await service.get(PlatformOwnerPrincipal(principal_id=owner_id), account_id)

        assert result.account_id == account_id
        assert result.state == "quarantine"
        try:
            await service.get(PlatformOwnerPrincipal(principal_id=uuid4()), account_id)
        except KeyError:
            pass
        else:
            raise AssertionError("another owner read an account status")

    asyncio.run(scenario())


def test_workspace_attempt_binds_telegram_authorization_to_actor_and_account_to_organization() -> None:
    async def scenario() -> None:
        organization_id, actor_id = uuid4(), uuid4()
        context = TenantContext(
            organization_id=organization_id,
            actor_id=actor_id,
            roles=frozenset({"company_owner"}),
        )
        phone, finalizer = FakePhoneAdapter(), FakeFinalizer()
        service = WorkspaceConnectionAttemptService(
            phone=phone,
            qr=FakeQrAdapter(),
            finalizer=finalizer,
        )

        started = await service.start_phone(context, "+12025550123")
        await service.submit_code(context, started.attempt_id, "needs-2fa")
        completed = await service.submit_password(context, started.attempt_id, "correct")

        assert completed.status is AttemptStatus.AUTHORIZED
        assert phone.owner_id == actor_id
        assert finalizer.calls == [
            (organization_id, 123456, b"TELETHON_STRING_SESSION\x00\x01phone-session")
        ]

    asyncio.run(scenario())


def test_workspace_account_directory_returns_only_current_organization_connection_states() -> None:
    async def scenario() -> None:
        organization_id, actor_id = uuid4(), uuid4()
        connected_account_id, unfinished_account_id = uuid4(), uuid4()
        last_seen_at = datetime.now(UTC)
        accounts = FakeDirectoryAccounts((connected_account_id, unfinished_account_id))
        service = WorkspaceAccountDirectoryService(
            accounts=accounts,
            connections=FakeDirectoryConnections(
                {
                    connected_account_id: ConnectionRecord(
                        account_id=connected_account_id,
                        session_ref=SessionRef(
                            account_id=connected_account_id,
                            session_id=uuid4(),
                            key_version=1,
                        ),
                        health=ConnectionHealth(
                            state="quarantine",
                            last_seen_at=last_seen_at,
                            proxy_ip=None,
                            latency_ms=None,
                            error_code=None,
                        ),
                    )
                }
            ),
        )

        result = await service.list(
            TenantContext(
                organization_id=organization_id,
                actor_id=actor_id,
                roles=frozenset({"manager"}),
            )
        )

        assert accounts.requested_organization_id == organization_id
        assert tuple(item.model_dump() for item in result) == (
            {
                "account_id": connected_account_id,
                "state": "quarantine",
                "last_seen_at": last_seen_at,
                "error_code": None,
            },
        )

    asyncio.run(scenario())
