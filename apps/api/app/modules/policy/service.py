"""Server-side enforcement for the Telegram/AI approval boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Generic, TypeVar
from uuid import UUID
from weakref import ReferenceType, ref

from .models import (
    AiApprovalRecord,
    AiOperation,
    AiOperationContext,
    ApprovalDecision,
    ApprovalGrantRequest,
    ChannelType,
    ContentOrigin,
    DataCategory,
    PlatformOwnerPrincipal,
    TermsRevision,
)
from .repository import ApprovalRepository, ApprovalRepositorySnapshot, _ApprovalWriter


class PolicyAuthorizationError(RuntimeError):
    """Safe denial raised by application guards and administration services."""


IssuedObject = TypeVar("IssuedObject")
CanonicalClaims = TypeVar("CanonicalClaims")


class _IssuedIdentityRegistry(Generic[IssuedObject, CanonicalClaims]):
    """Retain canonical claims server-side and resolve only the exact issued object."""

    def __init__(self) -> None:
        self._entries: dict[int, tuple[ReferenceType[IssuedObject], CanonicalClaims]] = {}
        self._lock = RLock()

    def remember(self, issued: IssuedObject, claims: CanonicalClaims) -> None:
        identity = id(issued)

        def discard(dead_ref: ReferenceType[IssuedObject]) -> None:
            with self._lock:
                current = self._entries.get(identity)
                if current is not None and current[0] is dead_ref:
                    self._entries.pop(identity, None)

        issued_ref = ref(issued, discard)
        with self._lock:
            self._entries[identity] = (issued_ref, claims)

    def resolve(self, candidate: object) -> CanonicalClaims | None:
        with self._lock:
            entry = self._entries.get(id(candidate))
            if entry is None or entry[0]() is not candidate:
                return None
            return entry[1]


@dataclass(frozen=True, slots=True)
class _ContextClaims:
    organization_id: UUID
    channel_type: ChannelType | None
    data_category: DataCategory
    operation: AiOperation
    origin: ContentOrigin

    def matches(self, context: object) -> bool:
        return (
            type(context) is AiOperationContext
            and context.organization_id == self.organization_id
            and type(context.organization_id) is UUID
            and context.channel_type is self.channel_type
            and context.data_category is self.data_category
            and context.operation is self.operation
            and context.origin is self.origin
        )

    def materialize(self) -> AiOperationContext:
        return AiOperationContext(
            organization_id=self.organization_id,
            channel_type=self.channel_type,
            data_category=self.data_category,
            operation=self.operation,
            origin=self.origin,
        )


class PolicyContextAuthority:
    """Server-owned issuer for organization-bound, allow-listed policy contexts."""

    def __init__(self) -> None:
        self._issued: _IssuedIdentityRegistry[AiOperationContext, _ContextClaims] = _IssuedIdentityRegistry()

    def real_telegram(
        self,
        *,
        organization_id: UUID,
        channel_type: ChannelType,
        data_category: DataCategory,
        operation: AiOperation,
    ) -> AiOperationContext:
        self._require_uuid(organization_id)
        self._require_exact_enum(channel_type, ChannelType)
        self._require_exact_enum(data_category, DataCategory)
        self._require_exact_enum(operation, AiOperation)
        context = AiOperationContext(
            organization_id=organization_id,
            channel_type=channel_type,
            data_category=data_category,
            operation=operation,
            origin=ContentOrigin.REAL_TELEGRAM,
        )
        self._issued.remember(
            context,
            _ContextClaims(
                organization_id=organization_id,
                channel_type=channel_type,
                data_category=data_category,
                operation=operation,
                origin=ContentOrigin.REAL_TELEGRAM,
            ),
        )
        return context

    def resolve(self, context: object) -> _ContextClaims | None:
        claims = self._issued.resolve(context)
        if claims is None or not claims.matches(context) or claims.origin is not ContentOrigin.REAL_TELEGRAM:
            return None
        return claims

    @staticmethod
    def _require_exact_enum(value: object, expected_type: type) -> None:
        if type(value) is not expected_type:
            raise TypeError("policy context requires allow-listed value objects")

    @staticmethod
    def _require_uuid(value: object) -> None:
        if type(value) is not UUID:
            raise TypeError("policy context requires an authoritative organization UUID")


class TrustedSyntheticPolicyAuthority:
    """Explicit test-harness issuer; production composition must not install it."""

    def __init__(self) -> None:
        self._issued: _IssuedIdentityRegistry[AiOperationContext, _ContextClaims] = _IssuedIdentityRegistry()

    def synthetic(
        self,
        *,
        organization_id: UUID,
        data_category: DataCategory,
        operation: AiOperation,
    ) -> AiOperationContext:
        PolicyContextAuthority._require_uuid(organization_id)
        PolicyContextAuthority._require_exact_enum(data_category, DataCategory)
        PolicyContextAuthority._require_exact_enum(operation, AiOperation)
        context = AiOperationContext(
            organization_id=organization_id,
            channel_type=None,
            data_category=data_category,
            operation=operation,
            origin=ContentOrigin.SYNTHETIC,
        )
        self._issued.remember(
            context,
            _ContextClaims(
                organization_id=organization_id,
                channel_type=None,
                data_category=data_category,
                operation=operation,
                origin=ContentOrigin.SYNTHETIC,
            ),
        )
        return context

    def resolve(self, context: object) -> _ContextClaims | None:
        claims = self._issued.resolve(context)
        if claims is None or not claims.matches(context) or claims.origin is not ContentOrigin.SYNTHETIC:
            return None
        return claims


class PlatformOwnerAuthority:
    """Server-owned issuer for the sole approval administration capability."""

    def __init__(self) -> None:
        self._issued: _IssuedIdentityRegistry[PlatformOwnerPrincipal, UUID] = _IssuedIdentityRegistry()

    def issue(self, principal_id: UUID) -> PlatformOwnerPrincipal:
        if type(principal_id) is not UUID:
            raise TypeError("principal ID must be an opaque UUID")
        principal = PlatformOwnerPrincipal(principal_id=principal_id)
        self._issued.remember(principal, principal_id)
        return principal

    def resolve(self, principal: object) -> UUID | None:
        canonical_id = self._issued.resolve(principal)
        if (
            canonical_id is None
            or type(principal) is not PlatformOwnerPrincipal
            or type(principal.principal_id) is not UUID
            or principal.principal_id != canonical_id
        ):
            return None
        return canonical_id


class PolicyGate:
    """Default-deny decision service that never accepts or returns message content."""

    def __init__(
        self,
        *,
        repository: ApprovalRepository,
        context_authority: PolicyContextAuthority,
        trusted_synthetic_authority: TrustedSyntheticPolicyAuthority | None = None,
        current_terms_revision: TermsRevision,
    ) -> None:
        if type(current_terms_revision) is not TermsRevision:
            raise TypeError("current terms revision must be a validated server value")
        self._repository = repository
        self._context_authority = context_authority
        self._trusted_synthetic_authority = trusted_synthetic_authority
        self._current_terms_revision = current_terms_revision

    async def evaluate(self, context: AiOperationContext) -> ApprovalDecision:
        claims = self._context_authority.resolve(context)
        if claims is None and self._trusted_synthetic_authority is not None:
            synthetic_claims = self._trusted_synthetic_authority.resolve(context)
            if synthetic_claims is not None:
                return ApprovalDecision(allowed=True, reason_code="synthetic_non_telegram")
        if claims is None:
            return ApprovalDecision(allowed=False, reason_code="context_untrusted")
        canonical_context = claims.materialize()

        try:
            snapshot = await self._repository.find_matching(canonical_context, self._current_terms_revision)
            snapshot = self._revalidate_snapshot(snapshot)
        except Exception:
            return ApprovalDecision(allowed=False, reason_code="approval_unavailable")

        record = snapshot.record
        if record is None:
            return ApprovalDecision(allowed=False, reason_code="approval_missing")
        try:
            matches = self._matches(record, canonical_context, snapshot.checked_at)
        except Exception:
            return ApprovalDecision(allowed=False, reason_code="approval_unavailable")
        if not matches:
            return ApprovalDecision(allowed=False, reason_code="approval_missing")
        return ApprovalDecision(
            allowed=True,
            reason_code="approval_matched",
            approval_id=record.approval_id,
        )

    async def require_ai_operation(self, context: AiOperationContext) -> ApprovalDecision:
        """Return the decision application guards must enforce before content load."""
        return await self.evaluate(context)

    @staticmethod
    def _revalidate_snapshot(snapshot: object) -> ApprovalRepositorySnapshot:
        if not isinstance(snapshot, ApprovalRepositorySnapshot):
            raise ValueError("invalid approval repository snapshot")
        checked_at = snapshot.checked_at
        if not isinstance(checked_at, datetime) or checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("invalid approval repository time")
        if snapshot.record is None:
            return ApprovalRepositorySnapshot(checked_at=checked_at.astimezone(UTC), record=None)
        if type(snapshot.record) is not AiApprovalRecord:
            raise ValueError("invalid approval repository row")
        values = {
            field_name: getattr(snapshot.record, field_name)
            for field_name in AiApprovalRecord.model_fields
        }
        record = AiApprovalRecord.model_validate(values)
        return ApprovalRepositorySnapshot(checked_at=checked_at.astimezone(UTC), record=record)

    def _matches(
        self,
        record: AiApprovalRecord,
        context: AiOperationContext,
        checked_at: datetime,
    ) -> bool:
        now = checked_at.astimezone(UTC)
        return (
            record.organization_id == context.organization_id
            and context.channel_type in record.channel_types
            and context.data_category in record.data_categories
            and context.operation in record.operations
            and record.terms_revision == self._current_terms_revision
            and record.approved_at <= now < record.expires_at
            and record.revoked_at is None
        )


class ApprovalAdministrationService:
    """Authorize immutable grant/revoke writes with a trusted server capability."""

    def __init__(self, *, writer: _ApprovalWriter, owner_authority: PlatformOwnerAuthority) -> None:
        self._writer = writer
        self._owner_authority = owner_authority

    async def create(
        self,
        request: ApprovalGrantRequest,
        principal: PlatformOwnerPrincipal,
    ) -> AiApprovalRecord:
        actor_id = self._require_owner(principal)
        return await self._writer.create(request, actor_id)

    async def revoke(
        self,
        approval_id: UUID,
        principal: PlatformOwnerPrincipal,
    ) -> AiApprovalRecord:
        actor_id = self._require_owner(principal)
        return await self._writer.revoke(approval_id, actor_id)

    def _require_owner(self, principal: object) -> UUID:
        principal_id = self._owner_authority.resolve(principal)
        if principal_id is None:
            raise PolicyAuthorizationError("platform owner capability required")
        return principal_id


LoadedContent = TypeVar("LoadedContent")


class PolicyProtectedMessageLoader:
    """Application seam that invokes a content loader only after an allow decision."""

    def __init__(self, gate: PolicyGate) -> None:
        self._gate = gate

    async def load(
        self,
        context: AiOperationContext,
        load_content: Callable[[], Awaitable[LoadedContent]],
    ) -> LoadedContent:
        decision = await self._gate.require_ai_operation(context)
        if not decision.allowed:
            raise PolicyAuthorizationError("AI operation is not approved")
        return await load_content()
