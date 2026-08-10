"""Default-deny acceptance tests for the Telegram/AI approval boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.policy.models import (
    AiApprovalRecord,
    AiOperation,
    ApprovalGrantRequest,
    ChannelType,
    DataCategory,
    TermsRevision,
)
from app.modules.policy.repository import ApprovalRepositorySnapshot, ApprovalRepositoryUnavailable
from app.modules.policy.routes import build_policy_router
from app.modules.policy.service import (
    ApprovalAdministrationService,
    PolicyAuthorizationError,
    PolicyContextAuthority,
    PolicyGate,
    PolicyProtectedMessageLoader,
    PlatformOwnerAuthority,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
ORG_A = UUID("10000000-0000-0000-0000-000000000001")
ORG_B = UUID("10000000-0000-0000-0000-000000000002")
OWNER = UUID("20000000-0000-0000-0000-000000000001")
APPROVAL_ID = UUID("30000000-0000-0000-0000-000000000001")
TERMS = TermsRevision("telegram-ai-2026-08-07")


def run(awaitable):
    return asyncio.run(awaitable)


class RecordingApprovalRepository:
    """Content-free test repository with a repository-authoritative clock."""

    def __init__(self, record: AiApprovalRecord | None = None, *, checked_at: datetime = NOW) -> None:
        self.snapshot = ApprovalRepositorySnapshot(checked_at=checked_at, record=record)
        self.failure: Exception | None = None
        self.find_calls = 0
        self.created: list[tuple[ApprovalGrantRequest, UUID]] = []
        self.revoked: list[tuple[UUID, UUID]] = []

    async def find_matching(self, context, terms_revision):
        self.find_calls += 1
        if self.failure is not None:
            raise self.failure
        return self.snapshot

    async def create(self, request: ApprovalGrantRequest, approved_by: UUID) -> AiApprovalRecord:
        self.created.append((request, approved_by))
        return make_record(
            organization_id=request.organization_id,
            channel_types=request.channel_types,
            data_categories=request.data_categories,
            operations=request.operations,
            terms_revision=request.terms_revision,
            evidence_uri=request.evidence_uri,
            approved_by=approved_by,
            expires_at=request.expires_at,
        )

    async def revoke(self, approval_id: UUID, revoked_by: UUID) -> AiApprovalRecord:
        self.revoked.append((approval_id, revoked_by))
        return make_record(approval_id=approval_id, revoked_at=NOW, approved_by=revoked_by)


def make_record(**updates) -> AiApprovalRecord:
    values = {
        "approval_id": APPROVAL_ID,
        "organization_id": ORG_A,
        "channel_types": frozenset({ChannelType.MTPROTO_USER}),
        "data_categories": frozenset({DataCategory.MESSAGE_TEXT}),
        "operations": frozenset({AiOperation.SUMMARIZE}),
        "terms_revision": TERMS,
        "evidence_uri": "https://evidence.invalid/approvals/30000000",
        "approved_by": OWNER,
        "approved_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=1),
        "revoked_at": None,
    }
    values.update(updates)
    return AiApprovalRecord(**values)


def make_grant_request(**updates) -> ApprovalGrantRequest:
    values = {
        "organization_id": ORG_A,
        "channel_types": frozenset({ChannelType.MTPROTO_USER}),
        "data_categories": frozenset({DataCategory.MESSAGE_TEXT}),
        "operations": frozenset({AiOperation.SUMMARIZE}),
        "terms_revision": TERMS,
        "evidence_uri": "https://evidence.invalid/approvals/30000000",
        "expires_at": NOW + timedelta(days=1),
    }
    values.update(updates)
    return ApprovalGrantRequest(**values)


@pytest.fixture
def context_authority() -> PolicyContextAuthority:
    return PolicyContextAuthority()


@pytest.fixture
def real_message_context(context_authority):
    def factory(
        *,
        organization_id: UUID = ORG_A,
        channel: ChannelType = ChannelType.MTPROTO_USER,
        data_category: DataCategory = DataCategory.MESSAGE_TEXT,
        operation: AiOperation = AiOperation.SUMMARIZE,
    ):
        return context_authority.real_telegram(
            organization_id=organization_id,
            channel_type=channel,
            data_category=data_category,
            operation=operation,
        )

    return factory


def make_gate(repository, authority) -> PolicyGate:
    return PolicyGate(repository=repository, context_authority=authority, current_terms_revision=TERMS)


def test_real_telegram_message_is_denied_without_matching_approval(context_authority, real_message_context):
    """Returning allow on a missing row would break the mandatory default deny."""
    repository = RecordingApprovalRepository()

    decision = run(make_gate(repository, context_authority).evaluate(real_message_context()))

    assert decision.allowed is False
    assert decision.reason_code == "approval_missing"


@pytest.mark.parametrize(
    "record",
    [
        make_record(expires_at=NOW),
        make_record(approved_at=NOW + timedelta(seconds=1), expires_at=NOW + timedelta(days=1)),
        make_record(organization_id=ORG_B),
        make_record(channel_types=frozenset({ChannelType.BOT_API})),
        make_record(data_categories=frozenset({DataCategory.MESSAGE_METADATA})),
        make_record(operations=frozenset({AiOperation.CLASSIFY})),
        make_record(terms_revision=TermsRevision("telegram-ai-older")),
        make_record(revoked_at=NOW - timedelta(minutes=1)),
    ],
    ids=["expired", "not-yet-effective", "wrong-org", "wrong-channel", "wrong-data", "wrong-operation", "wrong-terms", "revoked"],
)
def test_every_approval_dimension_must_match_exactly(record, context_authority, real_message_context):
    """Dropping any one comparison would let an inapplicable approval authorize content."""
    repository = RecordingApprovalRepository(record)

    decision = run(make_gate(repository, context_authority).evaluate(real_message_context()))

    assert decision.allowed is False
    assert decision.reason_code == "approval_missing"


def test_exact_current_approval_allows_real_telegram_metadata(context_authority, real_message_context):
    """Rejecting a fully matching current row would make the configured gate unusable."""
    repository = RecordingApprovalRepository(make_record())

    decision = run(make_gate(repository, context_authority).require_ai_operation(real_message_context()))

    assert decision.allowed is True
    assert decision.reason_code == "approval_matched"
    assert decision.approval_id == APPROVAL_ID


def test_repository_time_is_authoritative_at_expiry_boundary(context_authority, real_message_context):
    """Using caller or process time could extend a row past the database-observed boundary."""
    repository = RecordingApprovalRepository(make_record(expires_at=NOW + timedelta(seconds=1)), checked_at=NOW + timedelta(seconds=1))

    decision = run(make_gate(repository, context_authority).evaluate(real_message_context()))

    assert decision.allowed is False


@pytest.mark.parametrize(
    "evidence_uri",
    [
        "https://user:password@evidence.invalid/approval",
        "https://evidence.invalid/approval?access_token=secret",
        "https://evidence.invalid/approval#private-fragment",
    ],
)
def test_evidence_uri_rejects_embedded_secret_channels(evidence_uri):
    """Persisting URI credentials, query tokens, or fragments would leak secret material."""
    with pytest.raises(ValueError, match="evidence URI"):
        make_record(evidence_uri=evidence_uri)


@pytest.mark.parametrize(
    "snapshot",
    [
        object(),
        SimpleNamespace(checked_at=datetime(2026, 8, 10, 12, 0), record=make_record()),
        SimpleNamespace(checked_at=NOW, record={"organization_id": str(ORG_A)}),
    ],
    ids=["missing-fields", "naive-repository-time", "malformed-row"],
)
def test_malformed_repository_results_fail_closed(snapshot, context_authority, real_message_context):
    """Trusting a partial or malformed repository result could accidentally create an allow."""
    repository = RecordingApprovalRepository()
    repository.snapshot = snapshot

    decision = run(make_gate(repository, context_authority).evaluate(real_message_context()))

    assert decision.allowed is False
    assert decision.reason_code == "approval_unavailable"


def test_unavailable_repository_fails_closed_without_leaking_error(context_authority, real_message_context):
    """A database outage must never degrade the gate to allow or disclose internals."""
    repository = RecordingApprovalRepository()
    repository.failure = RuntimeError("database-password=do-not-leak")

    decision = run(make_gate(repository, context_authority).evaluate(real_message_context()))

    assert decision.allowed is False
    assert decision.reason_code == "approval_unavailable"
    assert "password" not in repr(decision)


def test_context_from_an_untrusted_issuer_is_denied_before_repository_access(context_authority):
    """Accepting another issuer's organization UUID would trust caller-selected tenancy."""
    foreign_authority = PolicyContextAuthority()
    context = foreign_authority.real_telegram(
        organization_id=ORG_A,
        channel_type=ChannelType.MTPROTO_USER,
        data_category=DataCategory.MESSAGE_TEXT,
        operation=AiOperation.SUMMARIZE,
    )
    repository = RecordingApprovalRepository(make_record())

    decision = run(make_gate(repository, context_authority).evaluate(context))

    assert decision.allowed is False
    assert decision.reason_code == "context_untrusted"
    assert repository.find_calls == 0


def test_server_issued_context_cannot_be_retargeted_after_issuance(context_authority, real_message_context):
    """Mutable organization or origin fields would let a valid capability be repurposed."""
    context = real_message_context()

    with pytest.raises(AttributeError):
        context.organization_id = ORG_B
    with pytest.raises(AttributeError):
        context.origin = "synthetic"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("channel_type", "mtproto_user"),
        ("data_category", "message_text"),
        ("operation", "summarize"),
    ],
)
def test_context_authority_rejects_arbitrary_strings(field, value, context_authority):
    """Coercing caller strings would expand the policy vocabulary outside its allow-list."""
    values = {
        "organization_id": ORG_A,
        "channel_type": ChannelType.MTPROTO_USER,
        "data_category": DataCategory.MESSAGE_TEXT,
        "operation": AiOperation.SUMMARIZE,
    }
    values[field] = value

    with pytest.raises(TypeError, match="allow-listed"):
        context_authority.real_telegram(**values)


def test_synthetic_non_telegram_evaluation_is_allowed_without_repository_access(context_authority):
    """Consulting Telegram approvals for explicitly server-issued synthetic input breaks the Stage 0 test seam."""
    repository = RecordingApprovalRepository()
    repository.failure = AssertionError("repository must not be called")
    context = context_authority.synthetic(
        organization_id=ORG_A,
        data_category=DataCategory.MESSAGE_TEXT,
        operation=AiOperation.SUMMARIZE,
    )

    decision = run(make_gate(repository, context_authority).evaluate(context))

    assert decision.allowed is True
    assert decision.reason_code == "synthetic_non_telegram"
    assert repository.find_calls == 0


def test_guard_aborts_before_loading_message_text_when_denied(context_authority, real_message_context):
    """Moving the content load ahead of the gate would expose raw Telegram text on denial."""
    loaded = 0

    async def load_message_text() -> str:
        nonlocal loaded
        loaded += 1
        return "raw Telegram content"

    guard = PolicyProtectedMessageLoader(make_gate(RecordingApprovalRepository(), context_authority))

    with pytest.raises(PolicyAuthorizationError, match="AI operation is not approved"):
        run(guard.load(real_message_context(), load_message_text))

    assert loaded == 0


def test_guard_loads_only_after_an_allowed_decision(context_authority, real_message_context):
    """Failing to invoke the loader after a valid decision would break the guard's success contract."""
    events: list[str] = []

    class OrderedRepository(RecordingApprovalRepository):
        async def find_matching(self, context, terms_revision):
            events.append("policy")
            return await super().find_matching(context, terms_revision)

    async def load_message_text() -> str:
        events.append("load")
        return "synthetic fixture text"

    guard = PolicyProtectedMessageLoader(make_gate(OrderedRepository(make_record()), context_authority))

    result = run(guard.load(real_message_context(), load_message_text))

    assert result == "synthetic fixture text"
    assert events == ["policy", "load"]


def test_only_a_principal_issued_by_the_server_authority_can_create_approval():
    """Trusting a role field or a capability from another issuer would permit forged approvals."""
    authority = PlatformOwnerAuthority()
    repository = RecordingApprovalRepository()
    service = ApprovalAdministrationService(repository=repository, owner_authority=authority)
    request = make_grant_request()

    with pytest.raises(PolicyAuthorizationError):
        run(service.create(request, {"id": str(OWNER), "role": "platform_owner"}))
    with pytest.raises(PolicyAuthorizationError):
        run(service.create(request, PlatformOwnerAuthority().issue(OWNER)))

    created = run(service.create(request, authority.issue(OWNER)))

    assert created.approved_by == OWNER
    assert repository.created == [(request, OWNER)]


def test_server_issued_owner_principal_cannot_change_actor_identity():
    """A mutable actor ID would let trusted authority be transferred after issuance."""
    principal = PlatformOwnerAuthority().issue(OWNER)

    with pytest.raises(AttributeError):
        principal.principal_id = ORG_B


def test_revocation_is_a_separate_owner_authorized_repository_action():
    """Mutating an approval directly would erase the immutable grant history."""
    authority = PlatformOwnerAuthority()
    repository = RecordingApprovalRepository(make_record())
    service = ApprovalAdministrationService(repository=repository, owner_authority=authority)

    revoked = run(service.revoke(APPROVAL_ID, authority.issue(OWNER)))

    assert revoked.approval_id == APPROVAL_ID
    assert revoked.revoked_at == NOW
    assert repository.revoked == [(APPROVAL_ID, OWNER)]


def test_routes_have_no_role_field_and_fail_closed_on_repository_errors():
    """Mapping internal write failures to raw responses would expose audit/database details."""
    authority = PlatformOwnerAuthority()
    repository = RecordingApprovalRepository()
    repository.failure = ApprovalRepositoryUnavailable("dsn contains secret")

    class FailingAdministrationService(ApprovalAdministrationService):
        async def create(self, request, principal):
            raise repository.failure

    async def trusted_principal():
        return authority.issue(OWNER)

    router = build_policy_router(
        FailingAdministrationService(repository=repository, owner_authority=authority),
        principal_dependency=trusted_principal,
    )
    payload = {
        "organization_id": str(ORG_A),
        "channel_types": ["mtproto_user"],
        "data_categories": ["message_text"],
        "operations": ["summarize"],
        "terms_revision": str(TERMS),
        "evidence_uri": "https://evidence.invalid/approvals/30000000",
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "role": "platform_owner",
    }

    with pytest.raises(ValidationError):
        ApprovalGrantRequest(**payload)
    payload.pop("role")
    request = ApprovalGrantRequest(**payload)
    create_route = next(route for route in router.routes if route.path == "/policy/ai-approvals")

    with pytest.raises(HTTPException) as failure:
        run(create_route.endpoint(request=request, principal=authority.issue(OWNER)))

    assert failure.value.status_code == 503
    assert failure.value.detail == "approval operation unavailable"
