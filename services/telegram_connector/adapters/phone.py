"""Server-side state machine for phone-number authorization."""

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from .base import SessionMaterial, SessionProbeResult


class PhoneAuthorizationClient(Protocol):
    """Minimal injected boundary around the networked Telegram client."""

    async def request_code(self, phone: str) -> object: ...

    async def sign_in(self, phone: str, code: str) -> tuple[int, str | None]: ...

    async def check_password(self, password: str) -> tuple[int, str | None]: ...

    async def export_session(self) -> bytes: ...


class AuthStep(BaseModel):
    """A deliberately non-secret view of one authorization challenge."""

    model_config = ConfigDict(frozen=True)

    state: Literal["code_sent", "needs_2fa", "authorized", "expired", "failed"]
    challenge_id: UUID
    expires_at: datetime
    safe_message: str


@dataclass
class _Challenge:
    expires_at: datetime
    owner_id: object
    client: object | None
    phone: str | None = None
    qr_token: object | None = None
    telegram_user_id: int | None = None
    state: str = "code_sent"


class _ChallengeStore:
    """Bounded private challenge state with atomic, owner-bound claims."""

    def __init__(self, *, ttl: timedelta, capacity: int, now: Callable[[], datetime]) -> None:
        if ttl < timedelta(0) or capacity < 1:
            raise ValueError("invalid authorization challenge configuration")
        self._ttl = ttl
        self._capacity = capacity
        self._now = now
        self._active: OrderedDict[UUID, _Challenge] = OrderedDict()
        self._expired: OrderedDict[UUID, tuple[datetime, object]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        owner_id: object,
        client: object,
        phone: str | None = None,
        qr_token: object | None = None,
    ) -> AuthStep:
        self._require_owner(owner_id)
        async with self._lock:
            self._prune_locked()
            challenge_id = uuid4()
            expires_at = self._utc_now() + self._ttl
            self._active[challenge_id] = _Challenge(
                expires_at=expires_at,
                owner_id=owner_id,
                client=client,
                phone=phone,
                qr_token=qr_token,
            )
            while len(self._active) > self._capacity:
                evicted_id, evicted = self._active.popitem(last=False)
                self._remember_expired_locked(evicted_id, evicted.expires_at, evicted.owner_id)
            return _step("code_sent", challenge_id, expires_at)

    async def failed(self) -> AuthStep:
        return _step("failed", uuid4(), self._utc_now())

    async def claim(
        self, challenge_id: UUID, owner_id: object, expected_state: str
    ) -> tuple[_Challenge | None, AuthStep | None]:
        """Claim before an await so only the claimant can invoke the client."""
        self._require_owner(owner_id)
        async with self._lock:
            self._prune_locked()
            challenge = self._active.get(challenge_id)
            if challenge is None:
                expired = self._expired.get(challenge_id)
                if expired is not None and expired[1] == owner_id:
                    return None, _step("expired", challenge_id, expired[0])
                return None, _step("failed", challenge_id, self._utc_now())
            if challenge.owner_id != owner_id:
                return None, _step("failed", challenge_id, challenge.expires_at)
            if challenge.expires_at <= self._utc_now():
                self._active.pop(challenge_id, None)
                self._remember_expired_locked(challenge_id, challenge.expires_at, challenge.owner_id)
                return None, _step("expired", challenge_id, challenge.expires_at)
            if challenge.state != expected_state:
                return None, _step("failed", challenge_id, challenge.expires_at)
            challenge.state = "processing"
            return challenge, None

    async def finish(self, challenge_id: UUID, challenge: _Challenge, state: str) -> AuthStep:
        """Finalize under the same lock, rechecking expiry after a client await."""
        async with self._lock:
            current = self._active.get(challenge_id)
            if current is not challenge:
                return _step("failed", challenge_id, self._utc_now())
            if current.expires_at <= self._utc_now():
                current.state = "expired"
                self._clear_secrets(current)
                self._active.pop(challenge_id, None)
                self._remember_expired_locked(challenge_id, current.expires_at, current.owner_id)
                return _step("expired", challenge_id, current.expires_at)
            current.state = state
            if state == "authorized":
                current.phone = None
                current.qr_token = None
            elif state in {"failed", "expired"}:
                self._clear_secrets(current)
            return _step(state, challenge_id, current.expires_at)

    async def qr_url(self, challenge_id: UUID, owner_id: object) -> str:
        """Read a QR deep link only for its bound, still-live challenge owner."""
        self._require_owner(owner_id)
        async with self._lock:
            self._prune_locked()
            challenge = self._active.get(challenge_id)
            if (
                challenge is None
                or challenge.owner_id != owner_id
                or challenge.state != "code_sent"
            ):
                raise ValueError("QR challenge unavailable")
            url = getattr(challenge.qr_token, "url", None)
            if not isinstance(url, str) or not url:
                raise ValueError("QR challenge unavailable")
            return url

    async def status(self, challenge_id: UUID, owner_id: object) -> AuthStep:
        """Observe challenge state without claiming its live Telegram operation."""
        self._require_owner(owner_id)
        async with self._lock:
            self._prune_locked()
            challenge = self._active.get(challenge_id)
            if challenge is None:
                expired = self._expired.get(challenge_id)
                if expired is not None and expired[1] == owner_id:
                    return _step("expired", challenge_id, expired[0])
                return _step("failed", challenge_id, self._utc_now())
            if challenge.owner_id != owner_id:
                return _step("failed", challenge_id, challenge.expires_at)
            state = "code_sent" if challenge.state == "processing" else challenge.state
            return _step(state, challenge_id, challenge.expires_at)

    def _prune_locked(self) -> None:
        now = self._utc_now()
        for challenge_id, challenge in tuple(self._active.items()):
            if challenge.expires_at <= now:
                self._active.pop(challenge_id, None)
                self._remember_expired_locked(challenge_id, challenge.expires_at, challenge.owner_id)
        for challenge_id, (expiry, _) in tuple(self._expired.items()):
            if expiry + self._ttl < now:
                self._expired.pop(challenge_id, None)

    def _remember_expired_locked(self, challenge_id: UUID, expiry: datetime, owner_id: object) -> None:
        self._expired[challenge_id] = (expiry, owner_id)
        while len(self._expired) > self._capacity:
            self._expired.popitem(last=False)

    @staticmethod
    def _clear_secrets(challenge: _Challenge) -> None:
        challenge.client = None
        challenge.phone = None
        challenge.qr_token = None

    @staticmethod
    def _require_owner(owner_id: object) -> None:
        if owner_id is None:
            raise ValueError("authenticated owner is required")

    def _utc_now(self) -> datetime:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("authorization clock must be timezone-aware")
        return now.astimezone(UTC)


_MESSAGES = {
    "code_sent": "Authorization code was requested.",
    "needs_2fa": "Two-factor authentication is required.",
    "authorized": "Authorization completed.",
    "expired": "Authorization challenge expired. Start again.",
    "failed": "Authorization could not be completed.",
}


def _step(state: str, challenge_id: UUID, expires_at: datetime) -> AuthStep:
    return AuthStep(
        state=state,
        challenge_id=challenge_id,
        expires_at=expires_at,
        safe_message=_MESSAGES[state],
    )


def _is_2fa_required(error: Exception) -> bool:
    return error.__class__.__name__ in {"PasswordRequired", "SessionPasswordNeededError"}


class PhoneAdapter:
    """Authorize a user through owner-bound, expiring phone challenges."""

    name = "phone"
    session_kind = "mtproto_user"

    def __init__(
        self,
        client_factory: Callable[[], PhoneAuthorizationClient],
        *,
        ttl: timedelta = timedelta(minutes=5),
        capacity: int = 128,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client_factory = client_factory
        self._challenges = _ChallengeStore(ttl=ttl, capacity=capacity, now=now)

    async def start(self, phone: str, owner_id: object) -> AuthStep:
        self._challenges._require_owner(owner_id)
        try:
            client = self._client_factory()
        except Exception:
            return await self._challenges.failed()
        step = await self._challenges.create(owner_id=owner_id, client=client, phone=phone)
        try:
            await client.request_code(phone)
        except Exception:
            challenge, terminal = await self._challenges.claim(step.challenge_id, owner_id, "code_sent")
            if challenge is None:
                return terminal or await self._challenges.failed()
            return await self._challenges.finish(step.challenge_id, challenge, "failed")
        challenge, terminal = await self._challenges.claim(step.challenge_id, owner_id, "code_sent")
        if challenge is None:
            return terminal or await self._challenges.failed()
        return await self._challenges.finish(step.challenge_id, challenge, "code_sent")

    async def submit_code(self, challenge_id: UUID, owner_id: object, code: str) -> AuthStep:
        challenge, terminal = await self._challenges.claim(challenge_id, owner_id, "code_sent")
        if challenge is None:
            return terminal or await self._challenges.failed()
        try:
            identity, _username = await cast(PhoneAuthorizationClient, challenge.client).sign_in(
                challenge.phone or "", code
            )
            challenge.telegram_user_id = _require_telegram_user_id(identity)
        except Exception as error:
            return await self._challenges.finish(
                challenge_id, challenge, "needs_2fa" if _is_2fa_required(error) else "failed"
            )
        return await self._challenges.finish(challenge_id, challenge, "authorized")

    async def submit_password(self, challenge_id: UUID, owner_id: object, password: str) -> AuthStep:
        challenge, terminal = await self._challenges.claim(challenge_id, owner_id, "needs_2fa")
        if challenge is None:
            return terminal or await self._challenges.failed()
        try:
            identity, _username = await cast(PhoneAuthorizationClient, challenge.client).check_password(password)
            challenge.telegram_user_id = _require_telegram_user_id(identity)
        except Exception:
            return await self._challenges.finish(challenge_id, challenge, "failed")
        return await self._challenges.finish(challenge_id, challenge, "authorized")

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
            payload = await cast(PhoneAuthorizationClient, challenge.client).export_session()
            if not isinstance(payload, bytes) or not payload:
                raise ValueError
            return challenge.telegram_user_id, payload
        except Exception:
            raise ValueError("authorized session unavailable") from None
        finally:
            await self._challenges.finish(challenge_id, challenge, "failed")

    async def probe(self, material: SessionMaterial) -> SessionProbeResult:
        return SessionProbeResult(adapter=self.name, state="needs_code", telegram_user_id=None, username=None, phone_masked=None, capabilities=frozenset(), error_code=None)

    async def convert(self, material: SessionMaterial) -> bytes:
        raise ValueError("interactive authorization cannot be imported")


def _require_telegram_user_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("invalid Telegram user identity")
    return value
