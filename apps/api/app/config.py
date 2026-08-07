"""API configuration shared by the composition root and database layer."""

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Configuration required for the API's PostgreSQL-backed policy gate."""

    database_url: PostgresDsn

    model_config = SettingsConfigDict(case_sensitive=False)
