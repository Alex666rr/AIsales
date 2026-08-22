"""Owner-bound orchestration over the connector's phone and QR state machines."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from telegram_connector.adapters.phone import AuthStep

from app.modules.policy.models import PlatformOwnerPrincipal
from app.modules.shared.commands import TenantContext

from .models import AttemptStatus, AttemptView, ConnectionMethod, ConnectionStatusView, QrStartView


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


class AccountOwnershipLookup(Protocol):
    async def organization_for_async(self, account_id: UUID) -> UUID | None: ...


class OrganizationAccountLookup(Protocol):
    async def list_account_ids_by_organization_async(
        self, organization_id: UUID
    ) -> tuple[UUID, ...]: ...


class ConnectionLookup(Protocol):
    async def get(self, account_id: UUID): ...


class ConnectionLifecycleRepository(ConnectionLookup, Protocol):
    async def save(self, record) -> None: ...

    async def force_terminal(self, account_id: UUID, state: str, now: datetime): ...


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


class ConnectionStatusService:
    """Return a connection state only after authoritative owner lookup."""

    def __init__(self, *, accounts: AccountOwnershipLookup, connections: ConnectionLookup) -> None:
        self._accounts = accounts
        self._connections = connections

    async def get(
        self, owner: PlatformOwnerPrincipal, account_id: UUID
    ) -> ConnectionStatusView:
        organization_id = await self._accounts.organization_for_async(account_id)
        if organization_id != owner.principal_id:
            raise KeyError("connection was not found")
        record = await self._connections.get(account_id)
        if record is None:
            raise KeyError("connection was not found")
        return ConnectionStatusView(
            account_id=account_id,
            state=record.health.state,
            last_seen_at=record.health.last_seen_at,
            error_code=record.health.error_code,
        )


class WorkspaceConnectionAttemptService:
    """Run browser-originated attempts using session-derived tenant context only."""

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

    async def start_phone(self, principal: TenantContext, phone: str) -> AttemptView:
        return _view(
            await self._phone.start(phone, principal.actor_id),
            ConnectionMethod.PHONE,
            _PHONE_STATES,
        )

    async def submit_code(
        self, principal: TenantContext, attempt_id: UUID, code: str
    ) -> AttemptView:
        step = await self._phone.submit_code(attempt_id, principal.actor_id, code)
        return await self._finalize_phone_if_authorized(principal, step)

    async def submit_password(
        self, principal: TenantContext, attempt_id: UUID, password: str
    ) -> AttemptView:
        step = await self._phone.submit_password(attempt_id, principal.actor_id, password)
        return await self._finalize_phone_if_authorized(principal, step)

    async def start_qr(self, principal: TenantContext) -> QrStartView:
        step, qr_url = await self._qr.start_background(principal.actor_id)
        view = _view(step, ConnectionMethod.QR, _QR_STATES)
        return QrStartView(**view.model_dump(), qr_url=qr_url)

    async def qr_status(self, principal: TenantContext, attempt_id: UUID) -> AttemptView:
        step = await self._qr.status(attempt_id, principal.actor_id)
        if step.state != "authorized":
            return _view(step, ConnectionMethod.QR, _QR_STATES)
        return await self._finalize(
            principal,
            step,
            ConnectionMethod.QR,
            self._qr.consume_authorized_session,
        )

    async def _finalize_phone_if_authorized(
        self, principal: TenantContext, step: AuthStep
    ) -> AttemptView:
        if step.state != "authorized":
            return _view(step, ConnectionMethod.PHONE, _PHONE_STATES)
        return await self._finalize(
            principal,
            step,
            ConnectionMethod.PHONE,
            self._phone.consume_authorized_session,
        )

    async def _finalize(
        self,
        principal: TenantContext,
        step: AuthStep,
        method: ConnectionMethod,
        consume,
    ) -> AttemptView:
        states = _PHONE_STATES if method is ConnectionMethod.PHONE else _QR_STATES
        if self._finalizer is None:
            return _view(step.model_copy(update={"state": "failed"}), method, states)
        try:
            telegram_user_id, session_payload = await consume(
                step.challenge_id, principal.actor_id
            )
            result = await self._finalizer.finalize(
                organization_id=principal.organization_id,
                telegram_user_id=telegram_user_id,
                session_payload=session_payload,
            )
            return _view(step, method, states).model_copy(
                update={"account_id": result.account_id}
            )
        except Exception:
            return _view(step.model_copy(update={"state": "failed"}), method, states)


class WorkspaceConnectionStatusService:
    """Read connection status only inside the signed-in organization."""

    def __init__(self, *, accounts: AccountOwnershipLookup, connections: ConnectionLookup) -> None:
        self._accounts = accounts
        self._connections = connections

    async def get(self, principal: TenantContext, account_id: UUID) -> ConnectionStatusView:
        organization_id = await self._accounts.organization_for_async(account_id)
        if organization_id != principal.organization_id:
            raise KeyError("connection was not found")
        record = await self._connections.get(account_id)
        if record is None:
            raise KeyError("connection was not found")
        return ConnectionStatusView(
            account_id=account_id,
            state=record.health.state,
            last_seen_at=record.health.last_seen_at,
            error_code=record.health.error_code,
        )


class WorkspaceAccountDirectoryService:
    """List redacted connection states only for the signed-in organization."""

    def __init__(
        self,
        *,
        accounts: OrganizationAccountLookup,
        connections: ConnectionLookup,
    ) -> None:
        self._accounts = accounts
        self._connections = connections

    async def list(self, principal: TenantContext) -> tuple[ConnectionStatusView, ...]:
        account_ids = await self._accounts.list_account_ids_by_organization_async(
            principal.organization_id
        )
        views: list[ConnectionStatusView] = []
        for account_id in account_ids:
            record = await self._connections.get(account_id)
            if record is None:
                continue
            views.append(
                ConnectionStatusView(
                    account_id=account_id,
                    state=record.health.state,
                    last_seen_at=record.health.last_seen_at,
                    error_code=record.health.error_code,
                )
            )
        return tuple(views)


class WorkspaceAccountControlService:
    """Owner-only lifecycle changes scoped to the account's organization."""

    def __init__(
        self,
        *,
        accounts: AccountOwnershipLookup,
        connections: ConnectionLifecycleRepository,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._accounts = accounts
        self._connections = connections
        self._now = now

    async def pause(self, principal: TenantContext, account_id: UUID) -> ConnectionStatusView:
        return await self._terminal(principal, account_id, "paused")

    async def archive(self, principal: TenantContext, account_id: UUID) -> ConnectionStatusView:
        return await self._terminal(principal, account_id, "archived")

    async def resume(self, principal: TenantContext, account_id: UUID) -> ConnectionStatusView:
        await self._require_owner_account(principal, account_id)
        record = await self._connections.get(account_id)
        if record is None:
            raise KeyError("connection was not found")
        if record.health.state != "paused":
            raise ValueError("only paused accounts can resume")
        resumed = record.model_copy(
            update={
                "health": record.health.model_copy(
                    update={"state": "quarantine", "last_seen_at": self._timestamp(), "error_code": None}
                )
            }
        )
        await self._connections.save(resumed)
        return _connection_status(resumed)

    async def _terminal(
        self, principal: TenantContext, account_id: UUID, state: str
    ) -> ConnectionStatusView:
        await self._require_owner_account(principal, account_id)
        return _connection_status(
            await self._connections.force_terminal(account_id, state, self._timestamp())
        )

    async def _require_owner_account(self, principal: TenantContext, account_id: UUID) -> None:
        if "company_owner" not in principal.roles:
            raise PermissionError("company owner required")
        organization_id = await self._accounts.organization_for_async(account_id)
        if organization_id != principal.organization_id:
            raise KeyError("connection was not found")

    def _timestamp(self) -> datetime:
        timestamp = self._now()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("connection clock must be timezone-aware")
        return timestamp.astimezone(UTC)


def _connection_status(record) -> ConnectionStatusView:
    return ConnectionStatusView(
        account_id=record.account_id,
        state=record.health.state,
        last_seen_at=record.health.last_seen_at,
        error_code=record.health.error_code,
    )


def _view(step: AuthStep, method: ConnectionMethod, states: dict[str, AttemptStatus]) -> AttemptView:
    return AttemptView(
        attempt_id=step.challenge_id,
        method=method,
        status=states.get(step.state, AttemptStatus.FAILED),
        expires_at=step.expires_at,
    )
