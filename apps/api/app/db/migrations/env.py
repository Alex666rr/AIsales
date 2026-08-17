"""Alembic environment for the prototype PostgreSQL schema."""

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from apps.api.app.db.base import Base
from apps.api.app.modules.audit import service as _audit_service  # noqa: F401
from apps.api.app.modules.policy import repository as _policy_repository  # noqa: F401
from apps.api.app.modules.shared import outbox as _outbox  # noqa: F401
from telegram_connector.persistence import gateway_metadata, telegram_state_metadata

config = context.config
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is required for migrations")
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = (Base.metadata, gateway_metadata, telegram_state_metadata)


def run_migrations_offline() -> None:
    """Generate SQL without opening a database connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the configured PostgreSQL database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
