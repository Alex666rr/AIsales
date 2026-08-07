"""Contract tests for safe, capacity-aware proxy assignment."""

import asyncio
from uuid import UUID

import pytest
from pydantic import ValidationError

from telegram_connector.proxies import (
    InMemoryProxyAssignmentRepository,
    ProxyAssignmentService,
    ProxyConfig,
    ProxyHealth,
)


class AvailableProxyChecker:
    async def check(self, proxy: ProxyConfig) -> ProxyHealth:
        return ProxyHealth(available=True, ip_address="203.0.113.40", latency_ms=31)


class UnavailableProxyChecker:
    async def check(self, proxy: ProxyConfig) -> ProxyHealth:
        return ProxyHealth(available=False, ip_address=None, latency_ms=None)


class SecretBearingCheckerFailure:
    async def check(self, proxy: ProxyConfig) -> ProxyHealth:
        raise RuntimeError("PROXY-PROBE-SENTINEL-SECRET")


@pytest.mark.parametrize(
    ("url", "expected_endpoint"),
    [
        ("socks5://edge.example:1080", "socks5://edge.example:1080"),
        ("http://edge.example:8080", "http://edge.example:8080"),
        ("https://edge.example:8443", "https://edge.example:8443"),
    ],
)
def test_proxy_supports_only_safe_socks_and_http_endpoints(url, expected_endpoint):
    """Changing the accepted scheme or host normalization must reject bad routing input."""
    proxy = ProxyConfig(proxy_id=UUID(int=1), url=url, capacity=1)

    assert proxy.endpoint == expected_endpoint
    assert proxy.client_url == expected_endpoint


@pytest.mark.parametrize(
    "url",
    [
        "ftp://edge.example:21",
        "socks5://edge.example:1080/hidden-path",
        "socks5://edge.example:1080?credential=leak",
        "socks5://@edge.example:1080",
        "socks5://bad host:1080",
        "socks5://edge.example",
    ],
)
def test_proxy_rejects_unsafe_or_ambiguous_urls_without_echoing_input(url):
    """Removing URL validation would permit malformed hosts or credentials into the client boundary."""
    with pytest.raises((ValueError, ValidationError)) as failure:
        ProxyConfig(proxy_id=UUID(int=1), url=url, capacity=1)

    assert "invalid proxy configuration" in str(failure.value)
    assert url not in str(failure.value)


def test_authenticated_proxy_redacts_credentials_from_every_public_representation():
    """Dropping field exclusions would disclose proxy passwords through repr or Pydantic dumps."""
    proxy = ProxyConfig(
        proxy_id=UUID(int=1),
        url="socks5://alice:very-secret-password@edge.example:1080",
        capacity=2,
    )

    for representation in (
        repr(proxy),
        proxy.model_dump(),
        proxy.model_dump_json(),
        proxy.model_dump(include={"url", "username", "password"}),
        proxy.model_dump_json(include={"url", "username", "password"}),
    ):
        assert "alice" not in str(representation)
        assert "very-secret-password" not in str(representation)

    assert proxy.endpoint == "socks5://edge.example:1080"
    assert proxy.client_url == "socks5://alice:very-secret-password@edge.example:1080"


def test_assignment_honors_capacity_and_account_override_atomically():
    """Replacing repository reservation with a read-then-write can overbook a proxy or ignore an override."""
    async def scenario():
        repository = InMemoryProxyAssignmentRepository(
            proxies=(
                ProxyConfig(proxy_id=UUID(int=1), url="socks5://one.example:1080", capacity=1),
                ProxyConfig(proxy_id=UUID(int=2), url="https://two.example:8443", capacity=5),
            ),
            default_proxy_id=UUID(int=1),
        )
        await repository.set_account_override(UUID(int=2), UUID(int=2))
        service = ProxyAssignmentService(repository, AvailableProxyChecker())

        first, overridden, blocked = await asyncio.gather(
            service.acquire(UUID(int=1)),
            service.acquire(UUID(int=2)),
            service.acquire(UUID(int=3)),
        )

        assert first.proxy.proxy_id == UUID(int=1)
        assert overridden.proxy.proxy_id == UUID(int=2)
        assert blocked is None
        assert await repository.assignments_for(UUID(int=1)) == (UUID(int=1),)
        assert await repository.assignments_for(UUID(int=2)) == (UUID(int=2),)

    asyncio.run(scenario())


def test_unavailable_proxy_is_not_assigned_as_usable_connection():
    """Ignoring the injected health check would start traffic through an unavailable proxy."""
    async def scenario():
        repository = InMemoryProxyAssignmentRepository(
            proxies=(ProxyConfig(proxy_id=UUID(int=1), url="http://edge.example:8080", capacity=1),),
            default_proxy_id=UUID(int=1),
        )
        assignment = await ProxyAssignmentService(repository, UnavailableProxyChecker()).acquire(UUID(int=1))

        assert assignment is not None
        assert assignment.health.available is False

    asyncio.run(scenario())


def test_probe_exception_becomes_safe_unavailable_health_without_secret_leak():
    """Letting checker exceptions escape would expose credentials through caller errors or logs."""
    async def scenario():
        repository = InMemoryProxyAssignmentRepository(
            proxies=(ProxyConfig(proxy_id=UUID(int=1), url="http://edge.example:8080", capacity=1),),
            default_proxy_id=UUID(int=1),
        )

        lease = await ProxyAssignmentService(repository, SecretBearingCheckerFailure()).acquire(UUID(int=1))

        assert lease is not None
        assert lease.health.available is False
        assert lease.health.error_code == "proxy_unavailable"
        assert "PROXY-PROBE-SENTINEL-SECRET" not in str(lease)
        assert "PROXY-PROBE-SENTINEL-SECRET" not in str(lease.model_dump())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("url", "kwargs"),
    [
        ("socks5://:password@edge.example:1080", {}),
        ("socks5://edge.example:1080", {"username": ""}),
        ("socks5://edge.example:1080", {"password": "password"}),
        ("socks5://url-user:url-password@edge.example:1080", {"username": "other"}),
    ],
)
def test_proxy_rejects_ambiguous_or_incomplete_separate_credentials_without_leaks(url, kwargs):
    """Accepting empty or conflicting credential forms creates an unsafe client URL boundary."""
    with pytest.raises(ValueError) as failure:
        ProxyConfig(proxy_id=UUID(int=1), url=url, **kwargs)

    assert str(failure.value) == "invalid proxy configuration"
    assert "password" not in str(failure.value)
    assert "url-user" not in str(failure.value)
