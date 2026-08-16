"""Safe, content-free views for interactive Telegram connection attempts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConnectionMethod(StrEnum):
    PHONE = "phone"
    QR = "qr"


class AttemptStatus(StrEnum):
    CODE_REQUESTED = "code_requested"
    PASSWORD_REQUIRED = "password_required"
    PENDING = "pending"
    AUTHORIZED = "authorized"
    EXPIRED = "expired"
    FAILED = "failed"


class AttemptView(BaseModel):
    """Public attempt state; it intentionally has no credential or QR fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: UUID
    method: ConnectionMethod
    status: AttemptStatus
    expires_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.status in {AttemptStatus.AUTHORIZED, AttemptStatus.EXPIRED, AttemptStatus.FAILED}

    def model_post_init(self, __context: object) -> None:
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("attempt expiry must be timezone-aware")
        object.__setattr__(self, "expires_at", self.expires_at.astimezone(UTC))


class QrStartView(AttemptView):
    """The one response that carries a short-lived QR deep link for scanning."""

    qr_url: str = Field(min_length=1, max_length=4096, repr=False)


class TdataConnectionView(BaseModel):
    """The safe result of accepting a locally converted tdata session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: UUID
    telegram_user_id: int = Field(gt=0)
    state: str = Field(pattern=r"^(quarantine|active)$")
