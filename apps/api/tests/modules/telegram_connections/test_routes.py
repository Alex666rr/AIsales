from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException

from app.main import create_app
from app.modules.policy.models import PlatformOwnerPrincipal
from app.modules.telegram_connections.models import AttemptStatus, AttemptView, ConnectionMethod
from app.modules.telegram_connections.routes import build_connection_router


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

    async def start_qr(self, owner) -> AttemptView:
        return AttemptView(attempt_id=uuid4(), method=ConnectionMethod.QR, status=AttemptStatus.PENDING, expires_at=datetime.now(UTC) + timedelta(minutes=2))

    async def qr_status(self, owner, attempt_id) -> AttemptView:
        return AttemptView(attempt_id=attempt_id, method=ConnectionMethod.QR, status=AttemptStatus.PENDING, expires_at=datetime.now(UTC))


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
