"""HTTP boundary contracts for tenant-scoped member administration."""

from __future__ import annotations

from app.modules.organizations.routes import CreateMemberRequest, MemberResponse


def test_member_creation_request_cannot_select_an_organization():
    """The authenticated tenant context, never a request body, decides the target organization."""
    fields = set(CreateMemberRequest.model_fields)

    assert fields == {"email", "role"}


def test_member_response_never_contains_credentials():
    assert set(MemberResponse.model_fields) == {"user_id", "email", "role"}
