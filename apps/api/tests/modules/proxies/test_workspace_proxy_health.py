from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

from app.main import create_app
from app.modules.shared.commands import TenantContext
from app.modules.proxies.models import ProxyView
from app.modules.proxies.routes import build_workspace_proxy_router
from app.modules.proxies.service import WorkspaceProxyService


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


class FakeWorkspaceProxies:
    async def list(self, _principal: TenantContext) -> tuple[ProxyView, ...]:
        return (
            ProxyView(
                proxy_id=UUID(int=1),
                endpoint="socks5://edge.example:1080",
                protocol="socks5",
                capacity=2,
                is_default=True,
                assignment_count=1,
                health="awaiting_check",
            ),
        )

    async def create(self, _principal: TenantContext, _url: str, _capacity: int, _default: bool) -> ProxyView:
        return ProxyView(
            proxy_id=UUID(int=2), endpoint="https://edge.example:443", protocol="https",
            capacity=1, is_default=False, assignment_count=0, health="awaiting_check",
        )


class FakeProxyRepository:
    async def list_for_organization(self, organization_id):
        assert organization_id == UUID(int=9)
        return (
            {
                "proxy_id": UUID(int=1),
                "endpoint": "https://edge.example:443",
                "capacity": 3,
                "is_default": False,
                "assignment_count": 0,
                "health": "awaiting_check",
            },
        )


def test_workspace_proxy_list_is_redacted_and_session_scoped() -> None:
    async def principal() -> TenantContext:
        return TenantContext(
            organization_id=uuid4(), actor_id=uuid4(), roles=frozenset({"company_owner"})
        )

    application = create_app()
    application.include_router(
        build_workspace_proxy_router(FakeWorkspaceProxies(), principal_dependency=principal)
    )

    status, body = asyncio.run(asgi_get(application, "/workspace/telegram/proxies"))

    assert status == 200
    assert json.loads(body) == [
        {
            "proxy_id": "00000000-0000-0000-0000-000000000001",
            "endpoint": "socks5://edge.example:1080",
            "protocol": "socks5",
            "capacity": 2,
            "is_default": True,
            "assignment_count": 1,
            "health": "awaiting_check",
        }
    ]
    assert b"password" not in body


def test_workspace_proxy_creation_never_echoes_credential_bearing_input() -> None:
    async def principal() -> TenantContext:
        return TenantContext(
            organization_id=uuid4(), actor_id=uuid4(), roles=frozenset({"company_owner"})
        )

    application = create_app()
    application.include_router(
        build_workspace_proxy_router(FakeWorkspaceProxies(), principal_dependency=principal)
    )

    status, body = asyncio.run(asgi_post(application, "/workspace/telegram/proxies", {
        "url": "https://user:secret-password@edge.example:443", "capacity": 1,
    }))

    assert status == 201
    assert json.loads(body)["endpoint"] == "https://edge.example:443"
    assert b"secret-password" not in body


def test_workspace_proxy_service_exposes_only_operational_proxy_fields() -> None:
    principal = TenantContext(
        organization_id=UUID(int=9), actor_id=uuid4(), roles=frozenset({"company_owner"})
    )

    views = asyncio.run(WorkspaceProxyService(FakeProxyRepository()).list(principal))

    assert views == (
        ProxyView(
            proxy_id=UUID(int=1),
            endpoint="https://edge.example:443",
            protocol="https",
            capacity=3,
            is_default=False,
            assignment_count=0,
            health="awaiting_check",
        ),
    )
