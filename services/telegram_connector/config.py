"""Validated configuration for the test-only Telegram connector."""

import base64
import binascii
import re
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

    @field_validator("session_encryption_key")
    @classmethod
    def require_32_byte_session_key(cls, value: SecretStr) -> SecretStr:
        decode_session_encryption_key(value.get_secret_value())
        return value


def decode_session_encryption_key(encoded: str) -> bytes:
    """Decode canonical URL-safe Base64 key material of exactly 32 bytes."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", encoded):
        raise ValueError("session encryption key must be URL-safe Base64 for exactly 32 bytes")
    try:
        padding = "=" * (-len(encoded) % 4)
        value = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
    except (binascii.Error, UnicodeError, ValueError):
        raise ValueError(
            "session encryption key must be URL-safe Base64 for exactly 32 bytes"
        ) from None
    if len(value) != 32:
        raise ValueError("session encryption key must decode to exactly 32 bytes")
    return value
