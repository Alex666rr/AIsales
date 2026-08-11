import base64
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.db.session import create_session_factory
from apps.api.app.config import ApiSettings
from apps.api.app.main import create_app
from telegram_connector.config import ConnectorSettings
from telegram_connector.models import (
    deserialize_utc_timestamp,
    serialize_utc_timestamp,
)


def set_required_connector_environment(monkeypatch) -> None:
    """Provide non-secret test values for all mandatory connector settings."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://runtime:password@localhost:5432/ai_sales")
    monkeypatch.setenv(
        "SESSION_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(b"k" * 32).decode("ascii"),
    )
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "test-hash")


def set_required_api_environment(monkeypatch) -> None:
    set_required_connector_environment(monkeypatch)
    monkeypatch.setenv("PLATFORM_OWNER_ID", "12345678-1234-4234-9234-123456789abc")
    monkeypatch.setenv("PLATFORM_OWNER_TOKEN", "t" * 32)
    monkeypatch.setenv("CURRENT_TERMS_REVISION", "terms-test")


def test_settings_reject_missing_encryption_key(monkeypatch):
    """A missing key must prevent the connector from starting."""
    set_required_connector_environment(monkeypatch)
    monkeypatch.delenv("SESSION_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValidationError):
        ConnectorSettings()


def test_settings_loads_required_test_environment(monkeypatch):
    """The connector reads all required prototype settings from the environment."""
    set_required_connector_environment(monkeypatch)

    settings = ConnectorSettings()

    assert str(settings.database_url) == "postgresql+psycopg://runtime:password@localhost:5432/ai_sales"
    assert settings.telegram_api_id == 12345
    assert settings.environment == "test"


@pytest.mark.parametrize("settings_type", [ConnectorSettings, ApiSettings])
def test_database_configuration_rejects_a_driver_that_cannot_serve_both_stacks(
    monkeypatch, settings_type
):
    """Allowing asyncpg would make synchronous Alembic and gateway construction fail at deployment."""
    set_required_connector_environment(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://runtime:password@localhost:5432/ai_sales",
    )
    monkeypatch.setenv("PLATFORM_OWNER_ID", "12345678-1234-4234-9234-123456789abc")
    monkeypatch.setenv("PLATFORM_OWNER_TOKEN", "t" * 32)
    monkeypatch.setenv("CURRENT_TERMS_REVISION", "terms-test")

    with pytest.raises(ValidationError, match="psycopg"):
        settings_type()


@pytest.mark.parametrize("settings_type", [ConnectorSettings, ApiSettings])
def test_settings_reject_encryption_keys_that_do_not_decode_to_exactly_32_bytes(
    monkeypatch, settings_type
):
    """Accepting AES-128 or AES-192 material would violate the deployed key contract."""
    set_required_api_environment(monkeypatch)
    monkeypatch.setenv(
        "SESSION_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(b"k" * 24).decode("ascii"),
    )

    with pytest.raises(ValidationError, match="32 bytes"):
        settings_type()


@pytest.mark.parametrize("settings_type", [ConnectorSettings, ApiSettings])
def test_settings_accept_urlsafe_base64_encoding_of_exactly_32_bytes(
    monkeypatch, settings_type
):
    """A correctly sized URL-safe key must survive settings validation."""
    set_required_api_environment(monkeypatch)
    monkeypatch.setenv(
        "SESSION_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(b"\xff" * 32).decode("ascii"),
    )

    loaded = settings_type()

    assert loaded.session_encryption_key.get_secret_value().startswith("_")


def test_api_settings_reject_non_v4_platform_owner_id(monkeypatch):
    """A nil or legacy UUID must not become the configured platform owner identity."""
    set_required_api_environment(monkeypatch)
    monkeypatch.setenv("PLATFORM_OWNER_ID", "12345678-1234-1234-9234-123456789abc")

    with pytest.raises(ValidationError, match="UUID v4"):
        ApiSettings()


def test_api_settings_reject_short_platform_owner_token(monkeypatch):
    """A trivially short bearer token must not protect platform-owner operations."""
    set_required_api_environment(monkeypatch)
    monkeypatch.setenv("PLATFORM_OWNER_TOKEN", "t" * 31)

    with pytest.raises(ValidationError, match="32"):
        ApiSettings()


def test_timestamp_serialization_normalizes_to_utc():
    """A timestamp with an offset is stored and restored as an aware UTC instant."""
    source = datetime(2026, 8, 7, 9, 30, tzinfo=timezone(timedelta(hours=7)))

    serialized = serialize_utc_timestamp(source)
    restored = deserialize_utc_timestamp(serialized)

    assert serialized == "2026-08-07T02:30:00Z"
    assert restored == datetime(2026, 8, 7, 2, 30, tzinfo=UTC)


def test_composition_root_exposes_prototype_health_check():
    """The API can be created without connecting to Telegram or PostgreSQL."""
    app = create_app()

    assert app.title == "AI Sales Manager Prototype"
    assert {route.path for route in app.routes} >= {"/healthz"}


def test_session_factory_creates_async_sqlalchemy_sessions():
    """Future policy-gate routes receive async database sessions."""
    pytest.importorskip("psycopg", reason="psycopg is installed by the Python 3.13 production image")
    factory = create_session_factory("postgresql+psycopg://runtime:password@localhost:5432/ai_sales")

    assert factory.class_ is AsyncSession
