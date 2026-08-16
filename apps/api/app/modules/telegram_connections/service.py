"""Owner-bound orchestration over the connector's phone and QR state machines."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from telegram_connector.adapters.phone import AuthStep

from app.modules.policy.models import PlatformOwnerPrincipal

from .models import AttemptStatus, AttemptView, ConnectionMethod, QrStartView


class PhoneAttemptAdapter(Protocol):
    async def start(self, phone: str, owner_id: UUID) -> AuthStep: ...
    async def submit_code(self, challenge_id: UUID, owner_id: UUID, code: str) -> AuthStep: ...
    async def submit_password(self, challenge_id: UUID, owner_id: UUID, password: str) -> AuthStep: ...

    async def consume_authorized_session(self, challenge_id: UUID, owner_id: UUID) -> tuple[int, bytes]: ...


class QrAttemptAdapter(Protocol):
    async def start_background(self, owner_id: UUID) -> tuple[AuthStep, str]: ...

    async def status(self, challenge_id: UUID, owner_id: UUID) -> AuthStep: ...

    async def consume_authorized_session(self, challenge_id: UUID, owner_id: UUID) -> tuple[int, bytes]: ...


class AuthorizedSessionFinalizer(Protocol):
    async def finalize(
        self, *, organization_id: UUID, telegram_user_id: int, session_payload: bytes
    ): ...


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

    def __init__(
        self,
        *,
        phone: PhoneAttemptAdapter,
        qr: QrAttemptAdapter,
        finalizer: AuthorizedSessionFinalizer | None = None,
    ) -> None:
        self._phone = phone
        self._qr = qr
        self._finalizer = finalizer

    async def start_phone(self, owner: PlatformOwnerPrincipal, phone: str) -> AttemptView:
        return _view(await self._phone.start(phone, owner.principal_id), ConnectionMethod.PHONE, _PHONE_STATES)

    async def submit_code(self, owner: PlatformOwnerPrincipal, attempt_id: UUID, code: str) -> AttemptView:
        step = await self._phone.submit_code(attempt_id, owner.principal_id, code)
        return await self._finalize_phone_if_authorized(owner, step)

    async def submit_password(self, owner: PlatformOwnerPrincipal, attempt_id: UUID, password: str) -> AttemptView:
        step = await self._phone.submit_password(attempt_id, owner.principal_id, password)
        return await self._finalize_phone_if_authorized(owner, step)

    async def start_qr(self, owner: PlatformOwnerPrincipal) -> QrStartView:
        step, qr_url = await self._qr.start_background(owner.principal_id)
        view = _view(step, ConnectionMethod.QR, _QR_STATES)
        return QrStartView(**view.model_dump(), qr_url=qr_url)

    async def qr_status(self, owner: PlatformOwnerPrincipal, attempt_id: UUID) -> AttemptView:
        step = await self._qr.status(attempt_id, owner.principal_id)
        if step.state != "authorized":
            return _view(step, ConnectionMethod.QR, _QR_STATES)
        return await self._finalize(owner, step, ConnectionMethod.QR, self._qr.consume_authorized_session)

    async def _finalize_phone_if_authorized(
        self, owner: PlatformOwnerPrincipal, step: AuthStep
    ) -> AttemptView:
        if step.state != "authorized":
            return _view(step, ConnectionMethod.PHONE, _PHONE_STATES)
        return await self._finalize(owner, step, ConnectionMethod.PHONE, self._phone.consume_authorized_session)

    async def _finalize(self, owner, step: AuthStep, method: ConnectionMethod, consume) -> AttemptView:
        if self._finalizer is None:
            return _view(step.model_copy(update={"state": "failed"}), method, _PHONE_STATES if method is ConnectionMethod.PHONE else _QR_STATES)
        try:
            telegram_user_id, session_payload = await consume(step.challenge_id, owner.principal_id)
            result = await self._finalizer.finalize(
                organization_id=owner.principal_id,
                telegram_user_id=telegram_user_id,
                session_payload=session_payload,
            )
            return _view(step, method, _PHONE_STATES if method is ConnectionMethod.PHONE else _QR_STATES).model_copy(
                update={"account_id": result.account_id}
            )
        except Exception:
            return _view(step.model_copy(update={"state": "failed"}), method, _PHONE_STATES if method is ConnectionMethod.PHONE else _QR_STATES)


def _view(step: AuthStep, method: ConnectionMethod, states: dict[str, AttemptStatus]) -> AttemptView:
    return AttemptView(
        attempt_id=step.challenge_id,
        method=method,
        status=states.get(step.state, AttemptStatus.FAILED),
        expires_at=step.expires_at,
    )
