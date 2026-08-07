"""Network-free contracts shared by all session authorization adapters."""

from collections.abc import Mapping
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class SessionMaterial(BaseModel):
    """Transient, redacted input passed to one adapter for probing or conversion."""

    model_config = ConfigDict(frozen=True)

    adapter: str
    payload: bytes = Field(repr=False)
    credentials: tuple[tuple[str, SecretStr], ...] = Field(default_factory=tuple, repr=False)

    @model_validator(mode="before")
    @classmethod
    def freeze_credentials(cls, values: object) -> object:
        """Convert caller mappings into an immutable, redacted credential value."""
        if not isinstance(values, Mapping):
            return values
        credentials = values.get("credentials")
        if isinstance(credentials, Mapping):
            return {**values, "credentials": tuple(sorted(credentials.items()))}
        return values


class SessionProbeResult(BaseModel):
    """Normalized, non-secret authorization state from a session adapter."""

    model_config = ConfigDict(frozen=True)

    adapter: str
    state: Literal["authorized", "needs_code", "needs_2fa", "invalid", "unsupported"]
    telegram_user_id: int | None
    username: str | None
    phone_masked: str | None
    capabilities: frozenset[str]
    error_code: str | None


@runtime_checkable
class SessionAdapter(Protocol):
    """The only contract through which later code may trigger Telegram effects."""

    name: str

    async def probe(self, material: SessionMaterial) -> SessionProbeResult:
        """Return a normalized authorization state without persisting input."""

    async def convert(self, material: SessionMaterial) -> bytes:
        """Convert transient input into session bytes for encrypted storage."""
