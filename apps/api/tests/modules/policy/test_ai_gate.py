"""Default-deny acceptance tests for the Telegram/AI approval boundary."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from importlib import import_module
from io import StringIO
from types import SimpleNamespace
from uuid import UUID

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.main import create_app
from app.modules.policy.models import (
    AiApprovalRecord,
    AiOperation,
    AiOperationContext,
    ApprovalGrantRequest,
    ChannelType,
    ContentOrigin,
    DataCategory,
    PlatformOwnerPrincipal,
    TermsRevision,
)
from app.modules.policy import service as policy_service
from app.modules.policy import repository as policy_repository
from app.modules.policy.repository import (
    ApprovalRepository,
    ApprovalRepositorySnapshot,
    ApprovalRepositoryUnavailable,
    SqlAlchemyApprovalRepository,
)
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


async def asgi_post_json(application, path: str, payload: dict[str, object]) -> tuple[int, bytes]:
    """Invoke the real ASGI stack without optional HTTP client dependencies."""
    body = json.dumps(payload).encode("utf-8")
    request_sent = False
    messages: list[dict[str, object]] = []

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

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
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    await application(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], response_body


def render_policy_migration(direction: str) -> str:
    """Render migration behavior through Alembic's PostgreSQL offline operations."""
    module = import_module("apps.api.app.db.migrations.versions.0001_policy_gate")
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://policy-test.invalid/prototype",
        opts={"as_sql": True, "literal_binds": True, "output_buffer": output},
    )
    module.op = Operations(context)
    getattr(module, direction)()
    return output.getvalue()


class RecordingApprovalRepository:
    """Content-free test repository with a repository-authoritative clock."""

    def __init__(self, record: AiApprovalRecord | None = None, *, checked_at: datetime = NOW) -> None:
        self.snapshot = ApprovalRepositorySnapshot(checked_at=checked_at, record=record)
        self.failure: Exception | None = None
        self.find_calls = 0

    async def find_matching(self, context, terms_revision):
        self.find_calls += 1
        if self.failure is not None:
            raise self.failure
        return self.snapshot



class RecordingApprovalWriter:
    """Test-only writer behind the owner-authorized administration boundary."""

    def __init__(self) -> None:
        self.created: list[tuple[ApprovalGrantRequest, UUID]] = []
        self.revoked: list[tuple[UUID, UUID]] = []

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


def test_context_and_principal_do_not_expose_copyable_issuer_tokens(context_authority, real_message_context):
    """A readable token would let a caller mint a second authority-equivalent object."""
    context = real_message_context()
    principal = PlatformOwnerAuthority().issue(OWNER)

    assert not hasattr(context, "_issuer_token")
    assert not hasattr(principal, "_issuer_token")


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("organization_id", ORG_B),
        ("channel_type", ChannelType.BOT_API),
        ("data_category", DataCategory.MESSAGE_METADATA),
        ("operation", AiOperation.CLASSIFY),
        ("origin", ContentOrigin.SYNTHETIC),
    ],
)
def test_object_setattr_retargeting_invalidates_issued_context(
    field,
    replacement,
    context_authority,
    real_message_context,
):
    """Canonical server claims must win over fields rewritten through object.__setattr__."""
    context = real_message_context()
    repository = RecordingApprovalRepository(make_record())
    object.__setattr__(context, field, replacement)

    decision = run(make_gate(repository, context_authority).evaluate(context))

    assert decision.allowed is False
    assert decision.reason_code == "context_untrusted"
    assert repository.find_calls == 0


def test_copy_of_valid_context_is_not_an_issued_context(context_authority, real_message_context):
    """Copying all visible fields must not copy server-side issuance authority."""
    copied = copy.copy(real_message_context())
    repository = RecordingApprovalRepository(make_record())

    decision = run(make_gate(repository, context_authority).evaluate(copied))

    assert decision.allowed is False
    assert decision.reason_code == "context_untrusted"
    assert repository.find_calls == 0


def test_constructed_context_with_copied_fields_is_not_issued(context_authority, real_message_context):
    """Constructing an equal value object must not forge exact-object issuance."""
    issued = real_message_context()
    values = {
        "organization_id": issued.organization_id,
        "channel_type": issued.channel_type,
        "data_category": issued.data_category,
        "operation": issued.operation,
        "origin": issued.origin,
    }
    if hasattr(issued, "_issuer_token"):
        values["_issuer_token"] = issued._issuer_token
    forged = AiOperationContext(**values)
    repository = RecordingApprovalRepository(make_record())

    decision = run(make_gate(repository, context_authority).evaluate(forged))

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


def test_production_context_authority_cannot_issue_synthetic_context(context_authority):
    """Production authority must not expose a synthetic relabeling path."""
    assert not hasattr(context_authority, "synthetic")


def test_explicit_trusted_synthetic_authority_can_issue_only_its_own_test_context(context_authority):
    """The synthetic bypass belongs to an independently injected test-only authority instance."""
    synthetic_authority_type = getattr(policy_service, "TrustedSyntheticPolicyAuthority", None)
    assert synthetic_authority_type is not None
    synthetic_authority = synthetic_authority_type()
    repository = RecordingApprovalRepository()
    repository.failure = AssertionError("repository must not be called")
    context = synthetic_authority.synthetic(
        organization_id=ORG_A,
        data_category=DataCategory.MESSAGE_TEXT,
        operation=AiOperation.SUMMARIZE,
    )
    gate = PolicyGate(
        repository=repository,
        context_authority=context_authority,
        trusted_synthetic_authority=synthetic_authority,
        current_terms_revision=TERMS,
    )

    decision = run(gate.evaluate(context))

    assert decision.allowed is True
    assert decision.reason_code == "synthetic_non_telegram"
    assert repository.find_calls == 0


def test_real_context_relabelled_synthetic_is_denied_by_both_authorities(context_authority, real_message_context):
    """A real-origin capability must not become synthetic by rewriting its visible origin."""
    synthetic_authority_type = getattr(policy_service, "TrustedSyntheticPolicyAuthority", None)
    assert synthetic_authority_type is not None
    synthetic_authority = synthetic_authority_type()
    repository = RecordingApprovalRepository(make_record())
    context = real_message_context()
    object.__setattr__(context, "origin", ContentOrigin.SYNTHETIC)
    gate = PolicyGate(
        repository=repository,
        context_authority=context_authority,
        trusted_synthetic_authority=synthetic_authority,
        current_terms_revision=TERMS,
    )

    decision = run(gate.evaluate(context))

    assert decision.allowed is False
    assert decision.reason_code == "context_untrusted"
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
    writer = RecordingApprovalWriter()
    service = ApprovalAdministrationService(writer=writer, owner_authority=authority)
    request = make_grant_request()

    with pytest.raises(PolicyAuthorizationError):
        run(service.create(request, {"id": str(OWNER), "role": "platform_owner"}))
    with pytest.raises(PolicyAuthorizationError):
        run(service.create(request, PlatformOwnerAuthority().issue(OWNER)))

    created = run(service.create(request, authority.issue(OWNER)))

    assert created.approved_by == OWNER
    assert writer.created == [(request, OWNER)]


def test_server_issued_owner_principal_cannot_change_actor_identity():
    """A mutable actor ID would let trusted authority be transferred after issuance."""
    principal = PlatformOwnerAuthority().issue(OWNER)

    with pytest.raises(AttributeError):
        principal.principal_id = ORG_B


@pytest.mark.parametrize("attack", ["mutate", "copy", "construct"])
def test_owner_authority_revalidates_exact_issued_identity_before_create(attack):
    """A copied, constructed, or rewritten owner object must not reach the writer."""
    authority = PlatformOwnerAuthority()
    issued = authority.issue(OWNER)
    if attack == "mutate":
        object.__setattr__(issued, "principal_id", ORG_B)
        attacked = issued
    elif attack == "copy":
        attacked = copy.copy(issued)
    else:
        values = {"principal_id": issued.principal_id}
        if hasattr(issued, "_issuer_token"):
            values["_issuer_token"] = issued._issuer_token
        attacked = PlatformOwnerPrincipal(**values)
    writer = RecordingApprovalWriter()
    service = ApprovalAdministrationService(writer=writer, owner_authority=authority)

    with pytest.raises(PolicyAuthorizationError):
        run(service.create(make_grant_request(), attacked))

    assert writer.created == []


def test_revocation_is_a_separate_owner_authorized_repository_action():
    """Mutating an approval directly would erase the immutable grant history."""
    authority = PlatformOwnerAuthority()
    writer = RecordingApprovalWriter()
    service = ApprovalAdministrationService(writer=writer, owner_authority=authority)

    revoked = run(service.revoke(APPROVAL_ID, authority.issue(OWNER)))

    assert revoked.approval_id == APPROVAL_ID
    assert revoked.revoked_at == NOW
    assert writer.revoked == [(APPROVAL_ID, OWNER)]


def test_public_approval_repository_contract_is_read_only():
    """Public persistence must not let a caller append grants or revocations with raw UUIDs."""
    assert "create" not in ApprovalRepository.__dict__
    assert "revoke" not in ApprovalRepository.__dict__
    assert not hasattr(SqlAlchemyApprovalRepository, "create")
    assert not hasattr(SqlAlchemyApprovalRepository, "revoke")


@pytest.mark.parametrize("untrusted", [OWNER, {"id": str(OWNER), "role": "platform_owner"}])
def test_raw_uuid_or_role_claim_cannot_reach_approval_writer(untrusted):
    """Only a currently revalidated principal object may append grant/audit history."""
    writer = RecordingApprovalWriter()
    service = ApprovalAdministrationService(writer=writer, owner_authority=PlatformOwnerAuthority())

    with pytest.raises(PolicyAuthorizationError):
        run(service.create(make_grant_request(), untrusted))

    assert writer.created == []
    assert writer.revoked == []


def test_model_constructed_malformed_approval_fails_closed(context_authority, real_message_context):
    """Pydantic model_construct must not bypass full row revalidation or escape as AttributeError."""
    malformed = AiApprovalRecord.model_construct(
        approval_id=APPROVAL_ID,
        organization_id=ORG_A,
        channel_types=frozenset({ChannelType.MTPROTO_USER}),
    )
    repository = RecordingApprovalRepository()
    repository.snapshot = ApprovalRepositorySnapshot(checked_at=NOW, record=malformed)

    decision = run(make_gate(repository, context_authority).evaluate(real_message_context()))

    assert decision.allowed is False
    assert decision.reason_code == "approval_unavailable"


def test_routes_have_no_role_field_and_fail_closed_on_repository_errors():
    """Mapping internal write failures to raw responses would expose audit/database details."""
    authority = PlatformOwnerAuthority()
    writer = RecordingApprovalWriter()
    failure = ApprovalRepositoryUnavailable("dsn contains secret")

    class FailingAdministrationService(ApprovalAdministrationService):
        async def create(self, request, principal):
            raise failure

    async def trusted_principal():
        return authority.issue(OWNER)

    router = build_policy_router(
        FailingAdministrationService(writer=writer, owner_authority=authority),
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


def test_asgi_validation_response_never_echoes_rejected_evidence_uri_or_secret(caplog):
    """Default 422 details must not echo rejected request input or leak it through handler logs."""
    authority = PlatformOwnerAuthority()

    async def trusted_principal():
        return authority.issue(OWNER)

    application = create_app()
    application.include_router(
        build_policy_router(
            ApprovalAdministrationService(writer=RecordingApprovalWriter(), owner_authority=authority),
            principal_dependency=trusted_principal,
        )
    )
    payload = {
        "organization_id": str(ORG_A),
        "channel_types": ["mtproto_user"],
        "data_categories": ["message_text"],
        "operations": ["summarize"],
        "terms_revision": str(TERMS),
        "evidence_uri": "https://evidence.invalid/approval?access_token=TOP-SECRET",
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
    }

    status_code, response_body = run(asgi_post_json(application, "/policy/ai-approvals", payload))

    assert status_code == 422
    assert json.loads(response_body) == {"detail": "request validation failed"}
    assert b"TOP-SECRET" not in response_body
    assert "TOP-SECRET" not in caplog.text


def test_policy_migration_blocks_truncate_and_public_direct_mutations():
    """Row immutability alone must not allow truncate or direct runtime-history writes."""
    sql = render_policy_migration("upgrade")

    for table_name in (
        "ai_approval_records",
        "ai_approval_revocations",
        "ai_approval_audit_events",
    ):
        assert f"BEFORE TRUNCATE ON {table_name}" in sql
        assert f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE {table_name} FROM PUBLIC" in sql


def test_policy_migration_pairs_history_writes_in_privilege_gated_functions():
    """The only grant/revoke entrypoints must pair domain history with its audit event."""
    sql = render_policy_migration("upgrade")
    grant_function = sql[sql.index("CREATE FUNCTION public.policy_grant_ai_approval"):]
    grant_function = grant_function[:grant_function.index("CREATE FUNCTION public.policy_revoke_ai_approval")]
    revoke_function = sql[sql.index("CREATE FUNCTION public.policy_revoke_ai_approval"):]

    assert "SECURITY DEFINER" in grant_function
    assert "SET search_path = pg_catalog" in grant_function
    assert "INSERT INTO public.ai_approval_records" in grant_function
    assert "INSERT INTO public.ai_approval_audit_events" in grant_function
    assert "SECURITY DEFINER" in revoke_function
    assert "INSERT INTO public.ai_approval_revocations" in revoke_function
    assert "ON CONFLICT ON CONSTRAINT pk_ai_approval_revocations DO NOTHING" in revoke_function
    assert "INSERT INTO public.ai_approval_audit_events" in revoke_function
    assert "REVOKE ALL ON FUNCTION public.policy_grant_ai_approval" in sql
    assert "REVOKE ALL ON FUNCTION public.policy_revoke_ai_approval" in sql
    assert " GRANT " not in sql


def test_private_writer_builds_only_db_controlled_grant_and_revoke_calls():
    """Application writer code must not regain a direct table-insert bypass."""
    writer_type = getattr(policy_repository, "_SqlAlchemyApprovalWriter")
    grant = writer_type._grant_statement(
        request=make_grant_request(),
        approval_id=APPROVAL_ID,
        event_id=UUID("40000000-0000-0000-0000-000000000001"),
        actor_id=OWNER,
    )
    revoke = writer_type._revoke_statement(
        approval_id=APPROVAL_ID,
        event_id=UUID("40000000-0000-0000-0000-000000000002"),
        actor_id=OWNER,
    )
    grant_sql = str(grant.compile(dialect=postgresql.dialect()))
    revoke_sql = str(revoke.compile(dialect=postgresql.dialect()))

    assert "policy_grant_ai_approval" in grant_sql
    assert "policy_revoke_ai_approval" in revoke_sql
    assert "INSERT" not in grant_sql.upper()
    assert "INSERT" not in revoke_sql.upper()


def test_policy_migration_security_objects_are_reversible():
    """Downgrade must remove both trigger classes and both controlled write functions."""
    sql = render_policy_migration("downgrade")

    assert "DROP FUNCTION IF EXISTS public.policy_grant_ai_approval" in sql
    assert "DROP FUNCTION IF EXISTS public.policy_revoke_ai_approval" in sql
    for table_name in (
        "ai_approval_records",
        "ai_approval_revocations",
        "ai_approval_audit_events",
    ):
        assert f"DROP TRIGGER IF EXISTS {table_name}_truncate_immutable ON {table_name}" in sql
