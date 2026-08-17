"""HTTP boundary contracts for credential-safe login."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.main import create_app
from app.modules.auth import routes
from app.modules.auth.models import AuthUser, RecoveryCodes, ServerSession
from app.modules.auth.passwords import hash_password
from app.modules.auth.routes import build_auth_router
from app.modules.auth.session_auth import SessionAuthenticator
from app.modules.auth.service import AuthService
from app.modules.organizations.models import UserRole
from app.modules.organizations.provisioning import PendingTotpEnrollment


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


async def asgi_get(application, path: str, *, headers: list[tuple[bytes, bytes]] | None = None) -> tuple[int, bytes]:
    messages: list[dict[str, object]] = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": headers or [],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 443),
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    response = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], response


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


def test_session_endpoint_returns_safe_authenticated_context_from_cookie():
    auth = service()
    application = create_app()
    application.include_router(build_auth_router(auth, session_authenticator=SessionAuthenticator(auth)))
    _status, _body, headers = asyncio.run(
        asgi_post(
            application,
            "/auth/login",
            {"email": "manager@example.test", "password": "correct password"},
        )
    )
    cookie = next(value for name, value in headers if name == b"set-cookie").split(b";", 1)[0]

    status, body = asyncio.run(asgi_get(application, "/auth/session", headers=[(b"cookie", cookie)]))

    assert status == 200
    assert json.loads(body) == {
        "actor_id": str(USER_ID),
        "organization_id": str(ORG_ID),
        "roles": ["manager"],
    }


def test_session_endpoint_rejects_an_expired_cookie_session():
    auth = service()
    application = create_app()
    application.include_router(build_auth_router(auth, session_authenticator=SessionAuthenticator(auth)))
    _status, _body, headers = asyncio.run(
        asgi_post(
            application,
            "/auth/login",
            {"email": "manager@example.test", "password": "correct password"},
        )
    )
    cookie = next(value for name, value in headers if name == b"set-cookie").split(b";", 1)[0]
    repository = auth._repository
    session_id = next(iter(repository.sessions))
    repository.sessions[session_id] = replace(
        repository.sessions[session_id],
        expires_at=NOW - timedelta(seconds=1),
    )

    status, _body = asyncio.run(asgi_get(application, "/auth/session", headers=[(b"cookie", cookie)]))

    assert status == 401


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

    def activate_setup_token(self, setup_token: str, *, password: str) -> PendingTotpEnrollment:
        self.calls.append((setup_token, password))
        return PendingTotpEnrollment(
            enrollment_id=USER_ID,
            enrollment_token="enrollment-token-only-once",
            totp_uri="otpauth://totp/AIsales:owner%40example.test?secret=scan-only",
        )


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

    assert status == 200
    assert json.loads(body) == {
        "enrollment_token": "enrollment-token-only-once",
        "totp_uri": "otpauth://totp/AIsales:owner%40example.test?secret=scan-only",
    }
    assert setup_token.encode() not in body
    assert password.encode() not in body
    assert activator.calls == [(setup_token, password)]


class RejectingSetupActivator:
    def activate_setup_token(self, setup_token: str, *, password: str) -> PendingTotpEnrollment:
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


class FakeTotpConfirmationService:
    def confirm_totp_enrollment(self, *, enrollment_token: str, code: str) -> RecoveryCodes:
        if enrollment_token != "enrollment-token-only-once" or code != "123456":
            raise AuthenticationDenied("not accepted")
        return RecoveryCodes(("recovery-1", "recovery-2"))


def test_totp_confirmation_returns_recovery_codes_only_after_valid_code():
    application = create_app()
    application.include_router(build_auth_router(FakeTotpConfirmationService()))

    status, body, _headers = asyncio.run(
        asgi_post(
            application,
            "/auth/totp/confirm",
            {"enrollment_token": "enrollment-token-only-once", "code": "123456"},
        )
    )

    assert status == 200
    assert json.loads(body) == {"recovery_codes": ["recovery-1", "recovery-2"]}
