"""Owner-bound orchestration over the connector's phone and QR state machines."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from telegram_connector.adapters.phone import AuthStep

from app.modules.policy.models import PlatformOwnerPrincipal

from .models import AttemptStatus, AttemptView, ConnectionMethod


class PhoneAttemptAdapter(Protocol):
    async def start(self, phone: str, owner_id: UUID) -> AuthStep: ...
    async def submit_code(self, challenge_id: UUID, owner_id: UUID, code: str) -> AuthStep: ...
    async def submit_password(self, challenge_id: UUID, owner_id: UUID, password: str) -> AuthStep: ...


class QrAttemptAdapter(Protocol):
    async def start(self, owner_id: UUID) -> AuthStep: ...
    async def complete(self, challenge_id: UUID, owner_id: UUID) -> AuthStep: ...


_PHONE_STATES = {
    "code_sent": AttemptStatus.CODE_REQUESTED,
    "needs_2fa": AttemptStatus.PASSWORD_REQUIRED,
    "authorized": AttemptStatus.AUTHORIZED,
    "expired": AttemptStatus.EXPIRED,
    "failed": AttemptStatus.FAILED,
}
_QR_STATES = {**_PHONE_STATES, "code_sent": AttemptStatus.PENDING}


class ConnectionAttemptService:
    """Translate trusted owner capabilities into redacted interactive attempt views."""

    def __init__(self, *, phone: PhoneAttemptAdapter, qr: QrAttemptAdapter) -> None:
        self._phone = phone
        self._qr = qr

    async def start_phone(self, owner: PlatformOwnerPrincipal, phone: str) -> AttemptView:
        return _view(await self._phone.start(phone, owner.principal_id), ConnectionMethod.PHONE, _PHONE_STATES)

    async def submit_code(self, owner: PlatformOwnerPrincipal, attempt_id: UUID, code: str) -> AttemptView:
        return _view(
            await self._phone.submit_code(attempt_id, owner.principal_id, code),
            ConnectionMethod.PHONE,
            _PHONE_STATES,
        )

    async def submit_password(self, owner: PlatformOwnerPrincipal, attempt_id: UUID, password: str) -> AttemptView:
        return _view(
            await self._phone.submit_password(attempt_id, owner.principal_id, password),
            ConnectionMethod.PHONE,
            _PHONE_STATES,
        )

    async def start_qr(self, owner: PlatformOwnerPrincipal) -> AttemptView:
        return _view(await self._qr.start(owner.principal_id), ConnectionMethod.QR, _QR_STATES)

    async def qr_status(self, owner: PlatformOwnerPrincipal, attempt_id: UUID) -> AttemptView:
        return _view(
            await self._qr.complete(attempt_id, owner.principal_id),
            ConnectionMethod.QR,
            _QR_STATES,
        )


def _view(step: AuthStep, method: ConnectionMethod, states: dict[str, AttemptStatus]) -> AttemptView:
    return AttemptView(
        attempt_id=step.challenge_id,
        method=method,
        status=states.get(step.state, AttemptStatus.FAILED),
        expires_at=step.expires_at,
    )
