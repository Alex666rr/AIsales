"""Server-side enforcement for the Telegram/AI approval boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

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
from .repository import ApprovalRepository, ApprovalRepositorySnapshot


class PolicyAuthorizationError(RuntimeError):
    """Safe denial raised by application guards and administration services."""


class PolicyContextAuthority:
    """Server-owned issuer for organization-bound, allow-listed policy contexts."""

    def __init__(self) -> None:
        self.__issuer_token = object()

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
        return AiOperationContext(
            _issuer_token=self.__issuer_token,
            organization_id=organization_id,
            channel_type=channel_type,
            data_category=data_category,
            operation=operation,
            origin=ContentOrigin.REAL_TELEGRAM,
        )

    def synthetic(
        self,
        *,
        organization_id: UUID,
        data_category: DataCategory,
        operation: AiOperation,
    ) -> AiOperationContext:
        self._require_uuid(organization_id)
        self._require_exact_enum(data_category, DataCategory)
        self._require_exact_enum(operation, AiOperation)
        return AiOperationContext(
            _issuer_token=self.__issuer_token,
            organization_id=organization_id,
            channel_type=None,
            data_category=data_category,
            operation=operation,
            origin=ContentOrigin.SYNTHETIC,
        )

    def owns(self, context: object) -> bool:
        return isinstance(context, AiOperationContext) and context._issuer_token is self.__issuer_token

    @staticmethod
    def _require_exact_enum(value: object, expected_type: type) -> None:
        if type(value) is not expected_type:
            raise TypeError("policy context requires allow-listed value objects")

    @staticmethod
    def _require_uuid(value: object) -> None:
        if type(value) is not UUID:
            raise TypeError("policy context requires an authoritative organization UUID")


class PlatformOwnerAuthority:
    """Server-owned issuer for the sole approval administration capability."""

    def __init__(self) -> None:
        self.__issuer_token = object()

    def issue(self, principal_id: UUID) -> PlatformOwnerPrincipal:
        if type(principal_id) is not UUID:
            raise TypeError("principal ID must be an opaque UUID")
        return PlatformOwnerPrincipal(_issuer_token=self.__issuer_token, principal_id=principal_id)

    def owns(self, principal: object) -> bool:
        return isinstance(principal, PlatformOwnerPrincipal) and principal._issuer_token is self.__issuer_token


class PolicyGate:
    """Default-deny decision service that never accepts or returns message content."""

    def __init__(
        self,
        *,
        repository: ApprovalRepository,
        context_authority: PolicyContextAuthority,
        current_terms_revision: TermsRevision,
    ) -> None:
        if type(current_terms_revision) is not TermsRevision:
            raise TypeError("current terms revision must be a validated server value")
        self._repository = repository
        self._context_authority = context_authority
        self._current_terms_revision = current_terms_revision

    async def evaluate(self, context: AiOperationContext) -> ApprovalDecision:
        if not self._context_authority.owns(context):
            return ApprovalDecision(allowed=False, reason_code="context_untrusted")
        if not self._context_is_well_formed(context):
            return ApprovalDecision(allowed=False, reason_code="context_untrusted")
        if context.origin is ContentOrigin.SYNTHETIC:
            return ApprovalDecision(allowed=True, reason_code="synthetic_non_telegram")

        try:
            snapshot = await self._repository.find_matching(context, self._current_terms_revision)
            if not self._valid_snapshot(snapshot):
                return ApprovalDecision(allowed=False, reason_code="approval_unavailable")
        except Exception:
            return ApprovalDecision(allowed=False, reason_code="approval_unavailable")

        record = snapshot.record
        if record is None:
            return ApprovalDecision(allowed=False, reason_code="approval_missing")
        if not self._matches(record, context, snapshot.checked_at):
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
    def _context_is_well_formed(context: AiOperationContext) -> bool:
        if type(context.organization_id) is not UUID:
            return False
        if type(context.data_category) is not DataCategory or type(context.operation) is not AiOperation:
            return False
        if type(context.origin) is not ContentOrigin:
            return False
        if context.origin is ContentOrigin.REAL_TELEGRAM:
            return type(context.channel_type) is ChannelType
        return context.origin is ContentOrigin.SYNTHETIC and context.channel_type is None

    @staticmethod
    def _valid_snapshot(snapshot: object) -> bool:
        if not isinstance(snapshot, ApprovalRepositorySnapshot):
            return False
        checked_at = snapshot.checked_at
        if not isinstance(checked_at, datetime) or checked_at.tzinfo is None or checked_at.utcoffset() is None:
            return False
        return snapshot.record is None or isinstance(snapshot.record, AiApprovalRecord)

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

    def __init__(self, *, repository: ApprovalRepository, owner_authority: PlatformOwnerAuthority) -> None:
        self._repository = repository
        self._owner_authority = owner_authority

    async def create(
        self,
        request: ApprovalGrantRequest,
        principal: PlatformOwnerPrincipal,
    ) -> AiApprovalRecord:
        actor_id = self._require_owner(principal)
        return await self._repository.create(request, actor_id)

    async def revoke(
        self,
        approval_id: UUID,
        principal: PlatformOwnerPrincipal,
    ) -> AiApprovalRecord:
        actor_id = self._require_owner(principal)
        return await self._repository.revoke(approval_id, actor_id)

    def _require_owner(self, principal: object) -> UUID:
        if not self._owner_authority.owns(principal):
            raise PolicyAuthorizationError("platform owner capability required")
        return principal.principal_id


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
