"""Safe proxy configuration, health checking, and atomic assignment contracts."""

import asyncio
import ipaddress
import re
from collections.abc import Iterable
from typing import Protocol
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


class ProxyConfigurationError(ValueError):
    """A deliberately non-diagnostic error for unsafe proxy input."""

    def __init__(self) -> None:
        super().__init__("invalid proxy configuration")


class ProxyConfig(BaseModel):
    """An immutable proxy definition whose credentials never serialize publicly."""

    model_config = ConfigDict(frozen=True)

    proxy_id: UUID
    endpoint: str
    capacity: int = Field(default=1, ge=1, le=5)
    username: SecretStr | None = Field(default=None, exclude=True, repr=False)
    password: SecretStr | None = Field(default=None, exclude=True, repr=False)

    def __init__(self, /, **data: object) -> None:
        """Accept a URL only at this guarded boundary and retain a redacted endpoint."""
        raw_url = data.pop("url", None)
        try:
            if raw_url is not None:
                if not isinstance(raw_url, str) or "endpoint" in data or "username" in data or "password" in data:
                    raise ProxyConfigurationError()
                endpoint, username, password = self._parse_url(raw_url)
                data["endpoint"] = endpoint
                if username is not None:
                    data["username"] = username
                if password is not None:
                    data["password"] = password
            else:
                endpoint = data.get("endpoint")
                if not isinstance(endpoint, str):
                    raise ProxyConfigurationError()
                endpoint, username, password = self._parse_url(endpoint)
                data["endpoint"] = endpoint
                if username is not None or password is not None:
                    raise ProxyConfigurationError()
            super().__init__(**data)
            if self.username is not None and not self.username.get_secret_value():
                raise ProxyConfigurationError()
            if self.password is not None and self.username is None:
                raise ProxyConfigurationError()
        except (ProxyConfigurationError, ValidationError, TypeError, ValueError) as error:
            if isinstance(error, ProxyConfigurationError):
                raise
            raise ProxyConfigurationError() from None

    @property
    def client_url(self) -> str:
        """Build the credential-bearing URL solely for the injected network client."""
        if self.username is None:
            return self.endpoint
        split = urlsplit(self.endpoint)
        user = quote(self.username.get_secret_value(), safe="")
        password = ""
        if self.password is not None:
            password = f":{quote(self.password.get_secret_value(), safe='')}"
        host = split.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        return f"{split.scheme}://{user}{password}@{host}:{split.port}"

    @staticmethod
    def _parse_url(raw_url: str) -> tuple[str, str | None, str | None]:
        try:
            split = urlsplit(raw_url)
            scheme = split.scheme.lower()
            if scheme not in {"socks5", "http", "https"}:
                raise ProxyConfigurationError()
            if split.path not in {"", "/"} or split.query or split.fragment:
                raise ProxyConfigurationError()
            if split.hostname is None or split.port is None:
                raise ProxyConfigurationError()
            if not 1 <= split.port <= 65535:
                raise ProxyConfigurationError()
            host = split.hostname.lower()
            ProxyConfig._validate_host(host)
            username = split.username
            password = split.password
            if username == "" or (password is not None and not username):
                raise ProxyConfigurationError()
            if "@" in host or any(character.isspace() for character in host):
                raise ProxyConfigurationError()
            formatted_host = f"[{host}]" if ":" in host else host
            return f"{scheme}://{formatted_host}:{split.port}", username, password
        except (ProxyConfigurationError, ValueError, UnicodeError):
            raise ProxyConfigurationError() from None

    @staticmethod
    def _validate_host(host: str) -> None:
        try:
            ipaddress.ip_address(host)
            return
        except ValueError:
            pass
        if len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise ProxyConfigurationError()
        labels = host.split(".")
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
            raise ProxyConfigurationError()


class ProxyHealth(BaseModel):
    """The non-secret result of an injected proxy probe."""

    model_config = ConfigDict(frozen=True)

    available: bool
    ip_address: str | None
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None


class ProxyReservation(BaseModel):
    """The authoritative assignment result, including cleanup ownership."""

    model_config = ConfigDict(frozen=True)

    proxy: ProxyConfig
    newly_reserved: bool
    account_override: bool
    assignment_id: UUID
    assignment_revision: int


class ProxyLease(BaseModel):
    """An assigned proxy and its latest health result."""

    model_config = ConfigDict(frozen=True)

    reservation: ProxyReservation
    health: ProxyHealth

    @property
    def proxy(self) -> ProxyConfig:
        """Expose the assigned proxy without exposing cleanup mechanics to clients."""
        return self.reservation.proxy


class ProxyHealthChecker(Protocol):
    """The only boundary at which a proxy may be contacted."""

    async def check(self, proxy: ProxyConfig) -> ProxyHealth:
        """Return a normalized, non-secret proxy status."""


class ProxyAssignmentRepository(Protocol):
    """PostgreSQL-compatible authoritative assignment boundary.

    ``reserve_assignment`` must resolve an override and enforce capacity in one
    transaction.  A supervisor never performs a read-then-write reservation.
    """

    async def reserve_assignment(self, account_id: UUID) -> ProxyReservation | None:
        """Atomically resolve and reserve this account's persisted proxy."""

    async def release_failed_reservation(self, account_id: UUID, reservation: ProxyReservation) -> None:
        """Release only a newly created non-override lease after failed startup."""

    async def release_terminal_assignment(self, account_id: UUID) -> None:
        """Release an un-overridden shared assignment after stop/archive."""


class InMemoryProxyAssignmentRepository:
    """A test-only stand-in for a transactional PostgreSQL assignment repository."""

    def __init__(self, *, proxies: Iterable[ProxyConfig], default_proxy_id: UUID | None) -> None:
        self._proxies = {proxy.proxy_id: proxy for proxy in proxies}
        if default_proxy_id is not None and default_proxy_id not in self._proxies:
            raise ValueError("unknown default proxy")
        self._default_proxy_id = default_proxy_id
        self._overrides: dict[UUID, UUID] = {}
        self._assignments: dict[UUID, tuple[UUID, UUID, int]] = {}
        self._revisions: dict[UUID, int] = {}
        self._lock = asyncio.Lock()

    async def set_account_override(self, account_id: UUID, proxy_id: UUID | None) -> None:
        """Persist an override; capacity is enforced on the next atomic reservation."""
        async with self._lock:
            self._revisions[account_id] = self._revisions.get(account_id, 0) + 1
            if proxy_id is None:
                self._overrides.pop(account_id, None)
            elif proxy_id in self._proxies:
                self._overrides[account_id] = proxy_id
            else:
                raise ValueError("unknown proxy")

    async def reserve_assignment(self, account_id: UUID) -> ProxyReservation | None:
        async with self._lock:
            override = self._overrides.get(account_id)
            selected = override if override is not None else self._default_proxy_id
            if selected is None:
                return None
            proxy = self._proxies[selected]
            current = self._assignments.get(account_id)
            revision = self._revisions.get(account_id, 0)
            if current is not None and current[0] == selected:
                return ProxyReservation(proxy=proxy, newly_reserved=False, account_override=override is not None, assignment_id=current[1], assignment_revision=current[2])
            assigned = sum(1 for proxy_id, _, _ in self._assignments.values() if proxy_id == selected)
            if assigned >= proxy.capacity:
                return None
            if current is not None:
                self._assignments.pop(account_id, None)
            assignment_id = uuid4()
            self._assignments[account_id] = (selected, assignment_id, revision)
            return ProxyReservation(proxy=proxy, newly_reserved=True, account_override=override is not None, assignment_id=assignment_id, assignment_revision=revision)

    async def release_failed_reservation(self, account_id: UUID, reservation: ProxyReservation) -> None:
        async with self._lock:
            if (
                reservation.newly_reserved
                and not reservation.account_override
                and self._assignments.get(account_id) == (reservation.proxy.proxy_id, reservation.assignment_id, reservation.assignment_revision)
                and self._revisions.get(account_id, 0) == reservation.assignment_revision
            ):
                self._assignments.pop(account_id, None)

    async def release_terminal_assignment(self, account_id: UUID) -> None:
        async with self._lock:
            if account_id not in self._overrides:
                self._assignments.pop(account_id, None)

    async def assignments_for(self, proxy_id: UUID) -> tuple[UUID, ...]:
        async with self._lock:
            return tuple(account_id for account_id, (assigned, _, _) in self._assignments.items() if assigned == proxy_id)


class ProxyAssignmentService:
    """Resolve one persisted proxy without performing rotation or network calls itself."""

    def __init__(self, repository: ProxyAssignmentRepository, checker: ProxyHealthChecker) -> None:
        self._repository = repository
        self._checker = checker

    async def acquire(self, account_id: UUID) -> ProxyLease | None:
        """Atomically reserve the configured proxy, then check that exact proxy only."""
        reservation = await self._repository.reserve_assignment(account_id)
        if reservation is None:
            return None
        try:
            health = await self._checker.check(reservation.proxy)
        except Exception:
            health = ProxyHealth(available=False, ip_address=None, latency_ms=None, error_code="proxy_unavailable")
        return ProxyLease(reservation=reservation, health=health)

    async def release_failed(self, account_id: UUID, lease: ProxyLease) -> None:
        """Apply the repository's atomic failed-start cleanup policy."""
        await self._repository.release_failed_reservation(account_id, lease.reservation)

    async def release_terminal(self, account_id: UUID) -> None:
        await self._repository.release_terminal_assignment(account_id)
