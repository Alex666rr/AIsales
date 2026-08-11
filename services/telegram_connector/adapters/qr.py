"""Server-side state machine for QR authorization."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID

from .base import SessionMaterial, SessionProbeResult
from .phone import AuthStep, _ChallengeStore


class QrAuthorizationClient(Protocol):
    async def request_qr(self) -> object: ...

    async def complete_qr(self, token: object) -> tuple[int, str | None]: ...


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
            await cast(QrAuthorizationClient, challenge.client).complete_qr(challenge.qr_token)
        except Exception as error:
            # A transient client failure may retry the same still-live challenge; QR expiry may not.
            return await self._challenges.finish(
                challenge_id, challenge, "expired" if _is_qr_expired(error) else "code_sent"
            )
        return await self._challenges.finish(challenge_id, challenge, "authorized")

    async def probe(self, material: SessionMaterial) -> SessionProbeResult:
        return SessionProbeResult(adapter=self.name, state="needs_code", telegram_user_id=None, username=None, phone_masked=None, capabilities=frozenset(), error_code=None)

    async def convert(self, material: SessionMaterial) -> bytes:
        raise ValueError("interactive authorization cannot be imported")
