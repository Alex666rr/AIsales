"""HTTP boundary contracts for tenant-scoped member administration."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI

from app.modules.organizations import routes
from app.modules.organizations.models import UserRole
from app.modules.organizations.routes import CreateMemberRequest, MemberResponse
from app.modules.organizations.provisioning import ProvisionedMember, ProvisionedOwner
from app.modules.policy.models import PlatformOwnerPrincipal
from app.modules.auth.models import AuthUser
from app.modules.organizations.workspace import OrganizationProfile


def test_member_creation_request_cannot_select_an_organization():
    """The authenticated tenant context, never a request body, decides the target organization."""
    fields = set(CreateMemberRequest.model_fields)

    assert fields == {"email", "role"}


def test_member_response_never_contains_credentials():
    assert set(MemberResponse.model_fields) == {"user_id", "email", "role"}


def test_platform_provisioning_request_does_not_accept_a_role_or_secret():
    request_type = routes.CreateOrganizationRequest

    assert set(request_type.model_fields) == {"organization_name", "owner_email"}


class FakeProvisioningService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def provision(self, organization_name: str, owner_email: str) -> ProvisionedOwner:
        self.calls.append((organization_name, owner_email))
        return ProvisionedOwner(
            organization_id=UUID("10000000-0000-0000-0000-000000000001"),
            owner_email=owner_email,
            setup_token="setup-token-only-once",
        )


class FakeStaffInvitationService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UserRole]] = []

    def invite(self, _context, *, email: str, role: UserRole) -> ProvisionedMember:
        self.calls.append((email, role))
        return ProvisionedMember(
            user_id=UUID("30000000-0000-0000-0000-000000000001"),
            email=email,
            role=role,
            setup_token="staff-setup-token-only-once",
        )


async def _trusted_owner() -> PlatformOwnerPrincipal:
    return PlatformOwnerPrincipal(UUID("20000000-0000-0000-0000-000000000001"))


async def _asgi_post(application: FastAPI, path: str, payload: dict[str, object]) -> tuple[int, bytes]:
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
    return start["status"], response


async def _asgi_request(
    application: FastAPI, method: str, path: str, payload: dict[str, object] | None = None
) -> tuple[int, bytes]:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
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
            "method": method,
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
    return start["status"], response


def test_platform_owner_route_provisions_the_first_company_owner():
    service = FakeProvisioningService()
    application = FastAPI()
    application.include_router(
        routes.build_platform_provisioning_router(
            service,
            principal_dependency=_trusted_owner,
        )
    )

    status, body = asyncio.run(
        _asgi_post(
            application,
            "/platform/organizations",
            {"organization_name": "Acme", "owner_email": "owner@example.test"},
        )
    )

    assert status == 201
    assert json.loads(body) == {
        "organization_id": "10000000-0000-0000-0000-000000000001",
        "owner_email": "owner@example.test",
        "setup_token": "setup-token-only-once",
    }
    assert service.calls == [("Acme", "owner@example.test")]


def test_company_owner_route_returns_a_one_time_staff_setup_token():
    service = FakeStaffInvitationService()
    application = FastAPI()
    application.include_router(
        routes.build_staff_invitation_router(
            service,
            principal_dependency=_trusted_tenant,
        )
    )

    status, body = asyncio.run(
        _asgi_post(
            application,
            "/organizations/members/invitations",
            {"email": "manager@example.test", "role": "manager"},
        )
    )

    assert status == 201
    assert json.loads(body) == {
        "user_id": "30000000-0000-0000-0000-000000000001",
        "email": "manager@example.test",
        "role": "manager",
        "setup_token": "staff-setup-token-only-once",
    }
    assert service.calls == [("manager@example.test", UserRole.MANAGER)]


async def _trusted_tenant():
    from app.modules.shared.commands import TenantContext

    return TenantContext(
        organization_id=UUID("10000000-0000-0000-0000-000000000001"),
        actor_id=UUID("20000000-0000-0000-0000-000000000001"),
        roles=frozenset({"company_owner"}),
    )


class FakeWorkspaceOrganizationService:
    def __init__(self) -> None:
        self.renamed_to: str | None = None
        self.deactivated: UUID | None = None
        self._organization_id = UUID("10000000-0000-0000-0000-000000000001")
        self._member_id = UUID("30000000-0000-0000-0000-000000000001")

    def profile(self, context) -> OrganizationProfile:
        return OrganizationProfile(context.organization_id, "AIsales")

    def rename(self, context, name: str) -> OrganizationProfile:
        self.renamed_to = name
        return OrganizationProfile(context.organization_id, name)

    def members(self, _context) -> tuple[AuthUser, ...]:
        return (
            AuthUser(
                id=self._member_id,
                organization_id=self._organization_id,
                email="manager@example.test",
                role=UserRole.MANAGER,
                password_hash="hash-never-returned",
                encrypted_totp_secret="secret-never-returned",
                recovery_code_hashes=("recovery-never-returned",),
            ),
        )

    def deactivate(self, _context, user_id: UUID) -> AuthUser:
        self.deactivated = user_id
        return AuthUser(
            id=user_id,
            organization_id=self._organization_id,
            email="manager@example.test",
            role=UserRole.MANAGER,
            password_hash="hash-never-returned",
            encrypted_totp_secret="secret-never-returned",
            recovery_code_hashes=("recovery-never-returned",),
            disabled_at=datetime.now(UTC),
        )


def test_workspace_routes_return_only_tenant_safe_profile_and_member_fields():
    service = FakeWorkspaceOrganizationService()
    application = FastAPI()
    application.include_router(routes.build_workspace_organization_router(service, principal_dependency=_trusted_tenant))

    status, body = asyncio.run(_asgi_request(application, "GET", "/workspace/organization"))
    assert status == 200
    assert json.loads(body) == {"organization_id": "10000000-0000-0000-0000-000000000001", "name": "AIsales"}

    status, body = asyncio.run(_asgi_request(application, "GET", "/workspace/members"))
    assert status == 200
    assert json.loads(body) == [{
        "user_id": "30000000-0000-0000-0000-000000000001",
        "email": "manager@example.test",
        "role": "manager",
        "is_active": True,
    }]
    assert b"hash-never-returned" not in body
    assert b"secret-never-returned" not in body
    assert b"recovery-never-returned" not in body


def test_workspace_owner_can_rename_organization_and_deactivate_member():
    service = FakeWorkspaceOrganizationService()
    application = FastAPI()
    application.include_router(routes.build_workspace_organization_router(service, principal_dependency=_trusted_tenant))
    member_id = "30000000-0000-0000-0000-000000000001"

    status, body = asyncio.run(_asgi_request(application, "PATCH", "/workspace/organization", {"name": "  AIsales Pro  "}))
    assert status == 200
    assert json.loads(body)["name"] == "  AIsales Pro  "
    assert service.renamed_to == "  AIsales Pro  "

    status, body = asyncio.run(_asgi_request(application, "POST", f"/workspace/members/{member_id}/deactivate"))
    assert status == 200
    assert json.loads(body)["is_active"] is False
    assert service.deactivated == UUID(member_id)
