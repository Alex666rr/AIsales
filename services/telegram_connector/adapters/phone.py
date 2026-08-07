"""Server-side state machine for phone-number authorization."""

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
    client: object
    phone: str | None = None
    qr_token: object | None = None
    state: str = "code_sent"


class _ChallengeStore:
    """Bounded in-memory state; its values are never serialized or logged."""

    def __init__(
        self,
        *,
        ttl: timedelta,
        capacity: int,
        now: Callable[[], datetime],
    ) -> None:
        if ttl < timedelta(0) or capacity < 1:
            raise ValueError("invalid authorization challenge configuration")
        self._ttl = ttl
        self._capacity = capacity
        self._now = now
        self._active: OrderedDict[UUID, _Challenge] = OrderedDict()
        self._expired: OrderedDict[UUID, datetime] = OrderedDict()

    def create(self, *, client: object, phone: str | None = None, qr_token: object | None = None) -> AuthStep:
        self._prune()
        challenge_id = uuid4()
        expires_at = self._utc_now() + self._ttl
        self._active[challenge_id] = _Challenge(
            expires_at=expires_at,
            client=client,
            phone=phone,
            qr_token=qr_token,
        )
        while len(self._active) > self._capacity:
            evicted_id, evicted = self._active.popitem(last=False)
            self._remember_expired(evicted_id, evicted.expires_at)
        return _step("code_sent", challenge_id, expires_at)

    def failed(self) -> AuthStep:
        """Return a safe failure view when no challenge could be created."""
        return _step("failed", uuid4(), self._utc_now())

    def get(self, challenge_id: UUID) -> tuple[_Challenge | None, AuthStep | None]:
        self._prune()
        challenge = self._active.get(challenge_id)
        if challenge is None:
            if challenge_id in self._expired:
                return None, _step("expired", challenge_id, self._expired[challenge_id])
            return None, _step("failed", challenge_id, self._utc_now())
        if challenge.expires_at <= self._utc_now():
            self._active.pop(challenge_id, None)
            self._remember_expired(challenge_id, challenge.expires_at)
            return None, _step("expired", challenge_id, challenge.expires_at)
        return challenge, None

    def step(self, challenge_id: UUID, challenge: _Challenge, state: str) -> AuthStep:
        challenge.state = state
        if state in {"authorized", "failed", "expired"}:
            challenge.phone = None
            challenge.qr_token = None
        return _step(state, challenge_id, challenge.expires_at)

    def _prune(self) -> None:
        now = self._utc_now()
        for challenge_id, challenge in tuple(self._active.items()):
            if challenge.expires_at <= now:
                self._active.pop(challenge_id, None)
                self._remember_expired(challenge_id, challenge.expires_at)
        for challenge_id, expiry in tuple(self._expired.items()):
            if expiry + self._ttl < now:
                self._expired.pop(challenge_id, None)

    def _remember_expired(self, challenge_id: UUID, expiry: datetime) -> None:
        self._expired[challenge_id] = expiry
        while len(self._expired) > self._capacity:
            self._expired.popitem(last=False)

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
    """Translate known client exception classes without reflecting their text."""
    return error.__class__.__name__ in {"PasswordRequired", "SessionPasswordNeededError"}


class PhoneAdapter:
    """Authorize a user through expiring, single-use phone challenges."""

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

    async def start(self, phone: str) -> AuthStep:
        try:
            client = self._client_factory()
        except Exception:
            return self._challenges.failed()
        step = self._challenges.create(client=client, phone=phone)
        try:
            await client.request_code(phone)
        except Exception:
            challenge, failure = self._challenges.get(step.challenge_id)
            if challenge is None:
                return failure or _step("failed", step.challenge_id, step.expires_at)
            return self._challenges.step(step.challenge_id, challenge, "failed")
        return step

    async def submit_code(self, challenge_id: UUID, code: str) -> AuthStep:
        challenge, terminal = self._challenges.get(challenge_id)
        if challenge is None:
            return terminal or _step("failed", challenge_id, datetime.now(UTC))
        if challenge.state != "code_sent":
            return _step("failed", challenge_id, challenge.expires_at)
        try:
            await cast(PhoneAuthorizationClient, challenge.client).sign_in(challenge.phone or "", code)
        except Exception as error:
            if _is_2fa_required(error):
                return self._challenges.step(challenge_id, challenge, "needs_2fa")
            return self._challenges.step(challenge_id, challenge, "failed")
        return self._challenges.step(challenge_id, challenge, "authorized")

    async def submit_password(self, challenge_id: UUID, password: str) -> AuthStep:
        challenge, terminal = self._challenges.get(challenge_id)
        if challenge is None:
            return terminal or _step("failed", challenge_id, datetime.now(UTC))
        if challenge.state != "needs_2fa":
            return _step("failed", challenge_id, challenge.expires_at)
        try:
            await cast(PhoneAuthorizationClient, challenge.client).check_password(password)
        except Exception:
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
