"""Content-free value objects for the Telegram/AI approval boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import core_schema


class ChannelType(StrEnum):
    """Telegram transports whose content requires an exact approval."""

    MTPROTO_USER = "mtproto_user"
    BOT_API = "bot_api"


class DataCategory(StrEnum):
    """Closed vocabulary for data that could be exposed to an AI operation."""

    MESSAGE_TEXT = "message_text"
    MESSAGE_METADATA = "message_metadata"
    ATTACHMENT_TEXT = "attachment_text"
    VOICE_TRANSCRIPT = "voice_transcript"


class AiOperation(StrEnum):
    """Closed vocabulary of AI operations understood by the policy gate."""

    DRAFT = "draft"
    AUTO_REPLY = "auto_reply"
    SUMMARIZE = "summarize"
    CLASSIFY = "classify"


class ContentOrigin(StrEnum):
    """Server-attested origin; callers cannot add arbitrary source labels."""

    REAL_TELEGRAM = "real_telegram"
    SYNTHETIC = "synthetic"


class TermsRevision(str):
    """Validated server configuration value for the terms being enforced."""

    _PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    def __new__(cls, value: str) -> "TermsRevision":
        if not isinstance(value, str) or cls._PATTERN.fullmatch(value) is None:
            raise ValueError("invalid terms revision")
        return str.__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())


@dataclass(frozen=True, slots=True)
class AiOperationContext:
    """Opaque, issuer-bound metadata; it deliberately cannot carry message content."""

    _issuer_token: object = field(repr=False)
    organization_id: UUID
    channel_type: ChannelType | None
    data_category: DataCategory
    operation: AiOperation
    origin: ContentOrigin

    def __repr__(self) -> str:
        return (
            "AiOperationContext(organization_id=<opaque>, "
            f"channel_type={self.channel_type!r}, data_category={self.data_category!r}, "
            f"operation={self.operation!r}, origin={self.origin!r})"
        )


@dataclass(frozen=True, slots=True)
class PlatformOwnerPrincipal:
    """Opaque server-minted capability, never a caller-provided role claim."""

    _issuer_token: object = field(repr=False)
    principal_id: UUID

    def __repr__(self) -> str:
        return "PlatformOwnerPrincipal(principal_id=<opaque>)"


class AiApprovalRecord(BaseModel):
    """Immutable approval history projected together with an optional revocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    channel_types: frozenset[ChannelType] = Field(min_length=1)
    data_categories: frozenset[DataCategory] = Field(min_length=1)
    operations: frozenset[AiOperation] = Field(min_length=1)
    terms_revision: TermsRevision
    evidence_uri: str = Field(min_length=1, max_length=2048)
    approved_by: UUID
    approved_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    @field_validator("approved_at", "expires_at", "revoked_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approval timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("evidence_uri")
    @classmethod
    def _safe_evidence_uri(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "urn"}:
            raise ValueError("evidence URI must use https or urn")
        if parsed.scheme == "https" and (not parsed.netloc or parsed.username or parsed.password):
            raise ValueError("invalid evidence URI")
        if parsed.query or parsed.fragment:
            raise ValueError("evidence URI cannot contain a query or fragment")
        return value

    @model_validator(mode="after")
    def _valid_window(self) -> "AiApprovalRecord":
        if self.expires_at <= self.approved_at:
            raise ValueError("approval expiry must be after approval time")
        if self.revoked_at is not None and self.revoked_at < self.approved_at:
            raise ValueError("revocation cannot predate approval")
        return self


class ApprovalGrantRequest(BaseModel):
    """Administrative intent; actor identity and approval time stay server-side."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: UUID
    channel_types: frozenset[ChannelType] = Field(min_length=1)
    data_categories: frozenset[DataCategory] = Field(min_length=1)
    operations: frozenset[AiOperation] = Field(min_length=1)
    terms_revision: TermsRevision
    evidence_uri: str = Field(min_length=1, max_length=2048)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def _aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approval expiry must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("evidence_uri")
    @classmethod
    def _safe_evidence_uri(cls, value: str) -> str:
        return AiApprovalRecord._safe_evidence_uri(value)


DecisionReason = Literal[
    "approval_matched",
    "approval_missing",
    "approval_unavailable",
    "context_untrusted",
    "synthetic_non_telegram",
]


class ApprovalDecision(BaseModel):
    """Safe, content-free result returned by the gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    reason_code: DecisionReason
    approval_id: UUID | None = None
