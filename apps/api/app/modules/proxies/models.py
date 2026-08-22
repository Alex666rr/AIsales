"""Public proxy views contain operational metadata only."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProxyView(BaseModel):
    """A redacted proxy record safe for the authenticated workspace UI."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proxy_id: UUID
    endpoint: str = Field(pattern=r"^(socks5|http|https)://")
    protocol: str = Field(pattern=r"^(socks5|http|https)$")
    capacity: int = Field(ge=1, le=5)
    is_default: bool
    assignment_count: int = Field(ge=0)
    health: str = Field(pattern=r"^(awaiting_check|healthy|degraded)$")
