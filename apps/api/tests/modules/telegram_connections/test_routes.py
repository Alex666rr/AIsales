from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException

from app.main import create_app
from app.modules.policy.models import PlatformOwnerPrincipal
from app.modules.telegram_connections.models import (
    AttemptStatus,
    AttemptView,
    ConnectionMethod,
    QrStartView,
    TdataConnectionView,
    ConnectionStatusView,
)
from app.modules.telegram_connections.routes import build_connection_router, build_tdata_ticket_router
from app.modules.telegram_connections.tdata_ticket import TdataTicketRegistry


async def asgi_post(application, path: str, payload: dict[str, object]) -> tuple[int, bytes]:
    body = json.dumps(payload).encode("utf-8")
    sent = False
    messages: list[dict[str, object]] = []

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    response = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], response


async def asgi_get(application, path: str) -> tuple[int, bytes]:
    sent = False
    messages: list[dict[str, object]] = []

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    response = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], response


class FakeAttempts:
    async def start_phone(self, owner, phone: str) -> AttemptView:
        return AttemptView(
            attempt_id=uuid4(),
            method=ConnectionMethod.PHONE,
            status=AttemptStatus.CODE_REQUESTED,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    async def submit_code(self, owner, attempt_id, code: str) -> AttemptView:
        return AttemptView(attempt_id=attempt_id, method=ConnectionMethod.PHONE, status=AttemptStatus.AUTHORIZED, expires_at=datetime.now(UTC))

    async def submit_password(self, owner, attempt_id, password: str) -> AttemptView:
        return AttemptView(attempt_id=attempt_id, method=ConnectionMethod.PHONE, status=AttemptStatus.AUTHORIZED, expires_at=datetime.now(UTC))

    async def start_qr(self, owner) -> QrStartView:
        return QrStartView(
            attempt_id=uuid4(),
            method=ConnectionMethod.QR,
            status=AttemptStatus.PENDING,
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
            qr_url="tg://login?token=QR-SENTINEL",
        )

    async def qr_status(self, owner, attempt_id) -> AttemptView:
        return AttemptView(attempt_id=attempt_id, method=ConnectionMethod.QR, status=AttemptStatus.PENDING, expires_at=datetime.now(UTC))


class FakeTdataHandoffs:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, bytes, bytes, bytes]] = []

    async def accept(self, owner, ticket_id, client_public_key, nonce, ciphertext) -> TdataConnectionView:
        self.calls.append((owner, ticket_id, client_public_key, nonce, ciphertext))
        return TdataConnectionView(account_id=uuid4(), telegram_user_id=123456, state="quarantine")


class FakeStatuses:
    async def get(self, owner, account_id) -> ConnectionStatusView:
        return ConnectionStatusView(
            account_id=account_id, state="quarantine", last_seen_at=None, error_code=None
        )


def test_phone_start_route_returns_only_safe_attempt_view() -> None:
    async def principal() -> PlatformOwnerPrincipal:
        return PlatformOwnerPrincipal(principal_id=uuid4())

    application = create_app()
    application.include_router(build_connection_router(FakeAttempts(), principal_dependency=principal))

    status, body = asyncio.run(asgi_post(application, "/telegram/connections/phone/start", {"phone": "+12025550123"}))

    assert status == 201
    assert json.loads(body)["status"] == "code_requested"
    assert "+12025550123" not in body.decode()


def test_phone_start_route_fails_before_reading_body_without_authenticated_owner() -> None:
    async def rejected_principal() -> PlatformOwnerPrincipal:
        raise HTTPException(status_code=401, detail="authentication required")

    application = create_app()
    application.include_router(build_connection_router(FakeAttempts(), principal_dependency=rejected_principal))

    status, body = asyncio.run(asgi_post(application, "/telegram/connections/phone/start", {"phone": "+12025550123"}))

    assert status == 401
    assert "+12025550123" not in body.decode()


def test_phone_route_validation_never_echoes_rejected_phone() -> None:
    async def principal() -> PlatformOwnerPrincipal:
        return PlatformOwnerPrincipal(principal_id=uuid4())

    application = create_app()
    application.include_router(build_connection_router(FakeAttempts(), principal_dependency=principal))

    status, body = asyncio.run(asgi_post(application, "/telegram/connections/phone/start", {"phone": "not-a-phone-secret"}))

    assert status == 422
    assert b"not-a-phone-secret" not in body


def test_qr_start_route_returns_short_lived_link_only_in_its_start_response() -> None:
    async def principal() -> PlatformOwnerPrincipal:
        return PlatformOwnerPrincipal(principal_id=uuid4())

    application = create_app()
    application.include_router(build_connection_router(FakeAttempts(), principal_dependency=principal))

    status, body = asyncio.run(asgi_post(application, "/telegram/connections/qr/start", {}))

    assert status == 201
    assert json.loads(body)["qr_url"] == "tg://login?token=QR-SENTINEL"


def test_tdata_ticket_route_returns_public_key_only_to_authenticated_owner() -> None:
    async def principal() -> PlatformOwnerPrincipal:
        return PlatformOwnerPrincipal(principal_id=uuid4())

    application = create_app()
    application.include_router(build_tdata_ticket_router(TdataTicketRegistry(), principal_dependency=principal))

    status, body = asyncio.run(asgi_post(application, "/telegram/connections/tdata/tickets", {}))

    response = json.loads(body)
    assert status == 201
    assert set(response) == {"ticket_id", "expires_at", "public_key"}


def test_tdata_handoff_accepts_only_encoded_envelope_and_never_echoes_session_bytes() -> None:
    async def principal() -> PlatformOwnerPrincipal:
        return PlatformOwnerPrincipal(principal_id=uuid4())

    handoffs = FakeTdataHandoffs()
    application = create_app()
    application.include_router(
        build_tdata_ticket_router(
            TdataTicketRegistry(), principal_dependency=principal, handoffs=handoffs
        )
    )
    ticket_id = uuid4()
    encrypted_session = b"TELETHON_STRING_SESSION\\x00\\x01must-never-return"

    status, body = asyncio.run(
        asgi_post(
            application,
            f"/telegram/connections/tdata/tickets/{ticket_id}/handoff",
            {
                "client_public_key": "Y2xpZW50LXB1YmxpYy1rZXk",
                "nonce": "bm9uY2U",
                "ciphertext": "VEVMRVRIT05fU1RSSU5HX1NFU1NJT04",
            },
        )
    )

    assert status == 201
    response = json.loads(body)
    assert set(response) == {"account_id", "telegram_user_id", "state"}
    assert encrypted_session not in body
    assert handoffs.calls == [
        (
            handoffs.calls[0][0],
            ticket_id,
            b"client-public-key",
            b"nonce",
            b"TELETHON_STRING_SESSION",
        )
    ]


def test_connection_status_route_exposes_only_safe_operational_state() -> None:
    async def principal() -> PlatformOwnerPrincipal:
        return PlatformOwnerPrincipal(principal_id=uuid4())

    account_id = uuid4()
    application = create_app()
    application.include_router(
        build_connection_router(FakeAttempts(), principal_dependency=principal, statuses=FakeStatuses())
    )

    status, body = asyncio.run(asgi_get(application, f"/telegram/connections/{account_id}"))

    assert status == 200
    assert json.loads(body) == {
        "account_id": str(account_id),
        "state": "quarantine",
        "last_seen_at": None,
        "error_code": None,
    }
