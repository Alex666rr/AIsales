from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.modules.policy.models import PlatformOwnerPrincipal
from app.modules.telegram_connections.models import AttemptStatus, ConnectionMethod
from app.modules.telegram_connections.service import ConnectionAttemptService
from telegram_connector.adapters.phone import AuthStep


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


class FakeQrAdapter:
    def __init__(self) -> None:
        self.challenge_id = uuid4()
        self.expires_at = datetime.now(UTC) + timedelta(minutes=2)

    async def start(self, owner_id: UUID) -> AuthStep:
        return AuthStep(
            state="code_sent",
            challenge_id=self.challenge_id,
            expires_at=self.expires_at,
            safe_message="safe",
        )

    async def complete(self, challenge_id: UUID, owner_id: UUID) -> AuthStep:
        return AuthStep(
            state="authorized",
            challenge_id=challenge_id,
            expires_at=self.expires_at,
            safe_message="safe",
        )


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


def test_qr_attempt_is_reported_as_pending_without_payload() -> None:
    service = ConnectionAttemptService(phone=FakePhoneAdapter(), qr=FakeQrAdapter())

    started = asyncio.run(service.start_qr(PlatformOwnerPrincipal(principal_id=uuid4())))

    assert started.method is ConnectionMethod.QR
    assert started.status is AttemptStatus.PENDING
    assert "token" not in repr(started).lower()
