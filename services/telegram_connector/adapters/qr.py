"""Server-side state machine for QR authorization."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID

from .base import SessionMaterial, SessionProbeResult
from .phone import AuthStep, _ChallengeStore


class QrAuthorizationClient(Protocol):
    async def request_qr(self) -> object: ...

    async def complete_qr(self, token: object) -> tuple[int, str | None]: ...

    async def export_session(self) -> bytes: ...


def _is_qr_expired(error: Exception) -> bool:
    return error.__class__.__name__ in {"QrExpired", "LoginTokenExpiredError"}


class QRAdapter:
    """Authorize a user using owner-bound, non-replayable QR challenges."""

    name = "qr"
    session_kind = "mtproto_user"

    def __init__(
        self,
        client_factory: Callable[[], QrAuthorizationClient],
        *,
        ttl: timedelta = timedelta(minutes=2),
        capacity: int = 128,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client_factory = client_factory
        self._challenges = _ChallengeStore(ttl=ttl, capacity=capacity, now=now)
        self._waiters: dict[UUID, asyncio.Task[AuthStep]] = {}

    async def start(self, owner_id: object) -> AuthStep:
        self._challenges._require_owner(owner_id)
        try:
            client = self._client_factory()
            token = await client.request_qr()
        except Exception:
            return await self._challenges.failed()
        return await self._challenges.create(owner_id=owner_id, client=client, qr_token=token)

    async def complete(self, challenge_id: UUID, owner_id: object) -> AuthStep:
        challenge, terminal = await self._challenges.claim(challenge_id, owner_id, "code_sent")
        if challenge is None:
            return terminal or await self._challenges.failed()
        try:
            identity, _username = await cast(QrAuthorizationClient, challenge.client).complete_qr(
                challenge.qr_token
            )
            if type(identity) is not int or identity <= 0:
                raise ValueError("invalid Telegram user identity")
            challenge.telegram_user_id = identity
        except Exception as error:
            # A transient client failure may retry the same still-live challenge; QR expiry may not.
            return await self._challenges.finish(
                challenge_id, challenge, "expired" if _is_qr_expired(error) else "code_sent"
            )
        return await self._challenges.finish(challenge_id, challenge, "authorized")

    async def start_background(self, owner_id: object) -> tuple[AuthStep, str]:
        """Begin Telegram's QR wait before returning the short-lived deep link."""
        step = await self.start(owner_id)
        if step.state != "code_sent":
            raise ValueError("QR challenge unavailable")
        try:
            url = await self._challenges.qr_url(step.challenge_id, owner_id)
        except Exception:
            challenge, _terminal = await self._challenges.claim(step.challenge_id, owner_id, "code_sent")
            if challenge is not None:
                await self._challenges.finish(step.challenge_id, challenge, "failed")
            raise ValueError("QR challenge unavailable") from None
        task = asyncio.create_task(self.complete(step.challenge_id, owner_id))
        self._waiters[step.challenge_id] = task
        task.add_done_callback(lambda finished: self._observe_waiter(step.challenge_id, finished))
        return step, url

    async def status(self, challenge_id: UUID, owner_id: object) -> AuthStep:
        step = await self._challenges.status(challenge_id, owner_id)
        if step.state in {"expired", "failed", "authorized"}:
            waiter = self._waiters.pop(challenge_id, None)
            if waiter is not None and not waiter.done():
                waiter.cancel()
        return step

    async def consume_authorized_session(
        self,
        challenge_id: UUID,
        owner_id: object,
    ) -> tuple[int, bytes]:
        """Claim the completed in-memory session once for immediate encryption."""
        challenge, _terminal = await self._challenges.claim(challenge_id, owner_id, "authorized")
        if challenge is None or challenge.telegram_user_id is None or challenge.client is None:
            raise ValueError("authorized session unavailable")
        try:
            payload = await cast(QrAuthorizationClient, challenge.client).export_session()
            if not isinstance(payload, bytes) or not payload:
                raise ValueError
            return challenge.telegram_user_id, payload
        except Exception:
            raise ValueError("authorized session unavailable") from None
        finally:
            await self._challenges.finish(challenge_id, challenge, "failed")

    def _observe_waiter(self, challenge_id: UUID, task: asyncio.Task[AuthStep]) -> None:
        self._waiters.pop(challenge_id, None)
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            pass

    async def probe(self, material: SessionMaterial) -> SessionProbeResult:
        return SessionProbeResult(adapter=self.name, state="needs_code", telegram_user_id=None, username=None, phone_masked=None, capabilities=frozenset(), error_code=None)

    async def convert(self, material: SessionMaterial) -> bytes:
        raise ValueError("interactive authorization cannot be imported")
