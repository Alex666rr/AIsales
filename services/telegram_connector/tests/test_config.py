from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.db.session import create_session_factory
from apps.api.app.main import create_app
from services.telegram_connector.config import ConnectorSettings
from services.telegram_connector.models import (
    deserialize_utc_timestamp,
    serialize_utc_timestamp,
)


def test_settings_reject_missing_encryption_key(monkeypatch):
    """A missing key must prevent the connector from starting."""
    monkeypatch.delenv("SESSION_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValidationError):
        ConnectorSettings()


def test_settings_loads_required_test_environment(monkeypatch):
    """The connector reads all required prototype settings from the environment."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_sales")
    monkeypatch.setenv("SESSION_ENCRYPTION_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "test-hash")

    settings = ConnectorSettings()

    assert str(settings.database_url) == "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_sales"
    assert settings.telegram_api_id == 12345
    assert settings.environment == "test"


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
    factory = create_session_factory("postgresql+asyncpg://postgres:postgres@localhost:5432/ai_sales")

    assert factory.class_ is AsyncSession
