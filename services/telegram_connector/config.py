"""Validated configuration for the test-only Telegram connector."""

from typing import Literal

from pydantic import PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConnectorSettings(BaseSettings):
    """Configuration loaded from the prototype environment without logging secrets."""

    database_url: PostgresDsn
    session_encryption_key: SecretStr
    telegram_api_id: int
    telegram_api_hash: SecretStr
    environment: Literal["test", "prototype"] = "test"

    model_config = SettingsConfigDict(case_sensitive=False)

    @field_validator("database_url")
    @classmethod
    def require_shared_psycopg_driver(cls, value: PostgresDsn) -> PostgresDsn:
        if str(value).partition("://")[0] != "postgresql+psycopg":
            raise ValueError("database URL must use postgresql+psycopg")
        return value
