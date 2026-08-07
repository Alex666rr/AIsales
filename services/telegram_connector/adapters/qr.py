"""Server-side state machine for QR authorization."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID

from .base import SessionMaterial, SessionProbeResult
from .phone import AuthStep, _ChallengeStore, _step


class QrAuthorizationClient(Protocol):
    async def request_qr(self) -> object: ...

    async def complete_qr(self, token: object) -> tuple[int, str | None]: ...


def _is_qr_expired(error: Exception) -> bool:
    return error.__class__.__name__ in {"QrExpired", "SessionPasswordNeededError", "LoginTokenExpiredError"}


class QRAdapter:
    """Authorize a user using an expiring, non-replayable QR challenge."""

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

    async def start(self) -> AuthStep:
        try:
            client = self._client_factory()
        except Exception:
            return self._challenges.failed()
        try:
            token = await client.request_qr()
        except Exception:
            # A generated ID still lets callers handle the failure uniformly without secrets.
            return self._challenges.create(client=client).model_copy(
                update={"state": "failed", "safe_message": "Authorization could not be completed."}
            )
        return self._challenges.create(client=client, qr_token=token)

    async def complete(self, challenge_id: UUID) -> AuthStep:
        challenge, terminal = self._challenges.get(challenge_id)
        if challenge is None:
            return terminal or _step("failed", challenge_id, datetime.now(UTC))
        if challenge.state != "code_sent":
            return self._challenges.step(challenge_id, challenge, "failed")
        try:
            await cast(QrAuthorizationClient, challenge.client).complete_qr(challenge.qr_token)
        except Exception as error:
            if _is_qr_expired(error):
                return self._challenges.step(challenge_id, challenge, "expired")
            return self._challenges.step(challenge_id, challenge, "failed")
        return self._challenges.step(challenge_id, challenge, "authorized")

    async def probe(self, material: SessionMaterial) -> SessionProbeResult:
        return SessionProbeResult(
            adapter=self.name,
            state="needs_code",
            telegram_user_id=None,
            username=None,
            phone_masked=None,
            capabilities=frozenset(),
            error_code=None,
        )

    async def convert(self, material: SessionMaterial) -> bytes:
        raise ValueError("interactive authorization cannot be imported")
