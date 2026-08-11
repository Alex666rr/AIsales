"""API configuration shared by the composition root and database layer."""

from uuid import UUID

from pydantic import Field, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from telegram_connector.config import decode_session_encryption_key


class ApiSettings(BaseSettings):
    """Configuration required for the API's PostgreSQL-backed policy gate."""

    database_url: PostgresDsn
    session_encryption_key: SecretStr
    session_encryption_key_version: int = Field(default=1, ge=1)
    telegram_api_id: int = Field(gt=0)
    telegram_api_hash: SecretStr
    platform_owner_id: UUID
    platform_owner_token: SecretStr = Field(min_length=32)
    current_terms_revision: str = Field(min_length=1, max_length=128)

    model_config = SettingsConfigDict(case_sensitive=False, extra="forbid")

    @field_validator("database_url")
    @classmethod
    def require_shared_psycopg_driver(cls, value: PostgresDsn) -> PostgresDsn:
        if str(value).partition("://")[0] != "postgresql+psycopg":
            raise ValueError("database URL must use postgresql+psycopg")
        return value

    @field_validator("session_encryption_key")
    @classmethod
    def require_32_byte_session_key(cls, value: SecretStr) -> SecretStr:
        decode_session_encryption_key(value.get_secret_value())
        return value

    @field_validator("platform_owner_id")
    @classmethod
    def require_uuid_v4_owner_id(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("platform owner ID must be a UUID v4")
        return value
