"""HTTP boundary contracts for credential-safe login."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from app.main import create_app
from app.modules.auth import routes
from app.modules.auth.models import AuthUser, ServerSession
from app.modules.auth.passwords import hash_password
from app.modules.auth.routes import build_auth_router
from app.modules.auth.service import AuthService
from app.modules.organizations.models import UserRole


ORG_ID = UUID("60000000-0000-0000-0000-000000000001")
USER_ID = UUID("70000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class FakeAuthRepository:
    def __init__(self) -> None:
        self.user = AuthUser(
            id=USER_ID,
            organization_id=ORG_ID,
            email="manager@example.test",
            role=UserRole.MANAGER,
            password_hash=hash_password("correct password"),
            encrypted_totp_secret=None,
            recovery_code_hashes=(),
        )
        self.sessions: dict[UUID, ServerSession] = {}

    def get_user_by_email(self, email: str) -> AuthUser | None:
        return self.user if email == self.user.email else None

    def save_user(self, user: AuthUser) -> None:
        self.user = user

    def save_session(self, session: ServerSession) -> None:
        self.sessions[session.id] = session

    def get_session(self, session_id: UUID) -> ServerSession | None:
        return self.sessions.get(session_id)


async def asgi_post(application, path: str, payload: dict[str, object]) -> tuple[int, bytes, list[tuple[bytes, bytes]]]:
    body = json.dumps(payload).encode("utf-8")
    received = False
    messages: list[dict[str, object]] = []

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 443),
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    response = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], response, start["headers"]


def service() -> AuthService:
    return AuthService(FakeAuthRepository(), encryption_key=b"x" * 32, now=lambda: NOW)


def test_login_sets_only_a_secure_http_only_session_cookie():
    application = create_app()
    application.include_router(build_auth_router(service()))

    status, body, headers = asyncio.run(
        asgi_post(
            application,
            "/auth/login",
            {"email": "manager@example.test", "password": "correct password"},
        )
    )

    assert status == 200
    assert json.loads(body) == {"mfa_verified": False}
    cookie = next(value for name, value in headers if name == b"set-cookie")
    assert b"HttpOnly" in cookie and b"Secure" in cookie and b"SameSite=lax" in cookie
    assert b"session_id" not in body


def test_login_rejects_invalid_password_without_echoing_it():
    application = create_app()
    application.include_router(build_auth_router(service()))
    password = "wrong-password-SENTINEL"

    status, body, _headers = asyncio.run(
        asgi_post(application, "/auth/login", {"email": "manager@example.test", "password": password})
    )

    assert status == 401
    assert password.encode() not in body


class FakeSetupActivator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def activate_setup_token(self, setup_token: str, *, password: str) -> UUID:
        self.calls.append((setup_token, password))
        return USER_ID


def test_setup_endpoint_uses_token_once_without_echoing_secrets():
    activator = FakeSetupActivator()
    application = create_app()
    application.include_router(routes.build_setup_router(activator))
    setup_token = "setup-token-SENTINEL"
    password = "a secure password SENTINEL"

    status, body, _headers = asyncio.run(
        asgi_post(
            application,
            "/auth/setup",
            {"setup_token": setup_token, "password": password},
        )
    )

    assert status == 204
    assert body == b""
    assert activator.calls == [(setup_token, password)]


class RejectingSetupActivator:
    def activate_setup_token(self, setup_token: str, *, password: str) -> UUID:
        raise PermissionError("expired setup-token-SENTINEL")


def test_setup_endpoint_rejects_a_token_without_echoing_it():
    application = create_app()
    application.include_router(routes.build_setup_router(RejectingSetupActivator()))
    setup_token = "setup-token-SENTINEL"

    status, body, _headers = asyncio.run(
        asgi_post(
            application,
            "/auth/setup",
            {"setup_token": setup_token, "password": "a secure password"},
        )
    )

    assert status == 401
    assert setup_token.encode() not in body
    assert b"expired" not in body
