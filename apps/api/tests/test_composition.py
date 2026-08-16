"""Production composition contracts for the Stage 0 API."""

import asyncio
import base64
from importlib import import_module
import json
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.config import ApiSettings
from app.main import create_app
from app.modules.policy.models import AiOperation, ChannelType, DataCategory
from app.modules.policy.service import PolicyAuthorizationError
from telegram_connector.persistence import create_gateway_schema


ACCOUNT = UUID(int=701)
ORGANIZATION = UUID(int=702)
OWNER_V4 = UUID("12345678-1234-4234-9234-123456789abc")

try:
    composition_module = import_module("app.composition")
except ModuleNotFoundError:
    composition_module = None


class UnusedAsyncSessions:
    def __call__(self):
        raise AssertionError("authentication denial must occur before database access")


async def asgi_post_json(application, path: str, payload: dict, headers=()):
    body = json.dumps(payload).encode("utf-8")
    messages = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), *headers],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    await application(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], response


async def asgi_get(application, path: str):
    messages = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
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
    }
    await application(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], response


def settings() -> ApiSettings:
    key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    return ApiSettings(
        database_url="postgresql+psycopg://runtime:password@db/ai_sales",
        session_encryption_key=key,
        telegram_api_id=12345,
        telegram_api_hash="test-api-hash",
        platform_owner_id=OWNER_V4,
        platform_owner_token="test-owner-token-that-is-at-least-32-characters",
        current_terms_revision="terms-2026-08",
    )


def build_test_composition(tmp_path):
    assert composition_module is not None, "application composition module is missing"
    assert hasattr(composition_module, "build_application_composition")
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'composition.db'}",
        connect_args={"check_same_thread": False},
    )
    create_gateway_schema(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    composition = composition_module.build_application_composition(
        settings(),
        sync_sessions=sessions,
        async_sessions=UnusedAsyncSessions(),
        sync_engine=engine,
    )
    return engine, composition


def test_policy_context_issuer_uses_persisted_account_ownership(tmp_path):
    """Accepting a caller organization would let one account borrow another tenant's approval."""

    async def scenario():
        engine, composition = build_test_composition(tmp_path)
        try:
            composition.account_repository.put(ACCOUNT, ORGANIZATION)
            context = await composition.policy_context_issuer.real_telegram(
                account_id=ACCOUNT,
                channel_type=ChannelType.MTPROTO_USER,
                data_category=DataCategory.MESSAGE_TEXT,
                operation=AiOperation.DRAFT,
            )
            assert context.organization_id == ORGANIZATION
            try:
                await composition.policy_context_issuer.real_telegram(
                    account_id=UUID(int=999),
                    channel_type=ChannelType.MTPROTO_USER,
                    data_category=DataCategory.MESSAGE_TEXT,
                    operation=AiOperation.DRAFT,
                )
            except PolicyAuthorizationError:
                pass
            else:
                raise AssertionError("unknown account received a policy context")
        finally:
            engine.dispose()

    asyncio.run(scenario())


def test_composed_app_mounts_authenticated_policy_routes_and_connector_services(tmp_path):
    """A health-only app or an unauthenticated owner route is not a production composition root."""
    engine, composition = build_test_composition(tmp_path)
    try:
        application = create_app(composition=composition)
        paths = set(application.openapi()["paths"])
        assert {
            "/healthz",
            "/auth/login",
            "/policy/ai-approvals",
            "/policy/ai-approvals/{approval_id}/revocations",
            "/telegram/connections/phone/start",
            "/telegram/connections/{attempt_id}/phone/confirm",
            "/telegram/connections/{attempt_id}/phone/password",
            "/telegram/connections/tdata/tickets",
            "/telegram/connections/tdata/tickets/{ticket_id}/handoff",
        } <= paths
        assert composition.adapter_registry.names == (
            "phone", "qr", "tdata", "telethon_file", "telethon_string", "bot"
        )
        assert type(composition.session_store._repository).__name__ == "SqlAlchemyCiphertextSessionRepository"
        assert type(composition.connection_repository).__name__ == "SqlAlchemyConnectionRepository"
        assert type(composition.proxy_repository).__name__ == "SqlAlchemyProxyAssignmentRepository"
        assert type(composition.gateway_repository).__name__ == "SqlAlchemyMessageDeliveryRepository"
        assert type(composition.auth_repository).__name__ == "SqlAlchemyAuthRepository"

        status, body = asyncio.run(
            asgi_post_json(
                application,
                "/policy/ai-approvals",
                {
                    "organization_id": str(ORGANIZATION),
                    "channel_types": ["mtproto_user"],
                    "data_categories": ["message_text"],
                    "operations": ["draft"],
                    "terms_revision": "terms-2026-08",
                    "evidence_uri": "urn:test:evidence",
                    "expires_at": "2026-12-01T00:00:00Z",
                },
            )
        )
        assert status == 401
        assert b"test-owner-token" not in body

        status, body = asyncio.run(
            asgi_post_json(
                application,
                "/telegram/connections/phone/start",
                {"phone": "+12025550123"},
            )
        )
        assert status == 401
        assert b"+12025550123" not in body
    finally:
        engine.dispose()


def test_production_app_factory_requires_complete_environment(monkeypatch):
    """Missing secrets must stop production construction instead of installing in-memory fallbacks."""
    assert composition_module is not None, "application composition module is missing"
    assert hasattr(composition_module, "create_production_composition")
    monkeypatch.delenv("SESSION_ENCRYPTION_KEY", raising=False)

    try:
        composition_module.create_production_composition()
    except Exception:
        pass
    else:
        raise AssertionError("production composition accepted incomplete configuration")


def test_health_check_fails_closed_without_database_composition():
    """An uncomposed API must not report ready when no database can be checked."""
    status, body = asyncio.run(asgi_get(create_app(), "/healthz"))

    assert status == 503
    assert json.loads(body) == {"status": "unavailable"}


def test_health_check_requires_database_at_current_migration_revision(tmp_path):
    """Reachable storage with a stale Alembic revision is not ready to serve requests."""
    engine, composition = build_test_composition(tmp_path)
    application = create_app(composition=composition)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES ('0001_policy_gate')")
            )

        stale_status, stale_body = asyncio.run(asgi_get(application, "/healthz"))

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE alembic_version SET version_num = "
                    "'0006_stage1_access'"
                )
            )
        current_status, current_body = asyncio.run(asgi_get(application, "/healthz"))

        assert stale_status == 503
        assert json.loads(stale_body) == {"status": "unavailable"}
        assert current_status == 200
        assert json.loads(current_body) == {
            "status": "ok",
            "environment": "prototype",
        }
    finally:
        engine.dispose()
