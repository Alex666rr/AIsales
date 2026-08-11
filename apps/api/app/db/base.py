"""SQLAlchemy metadata root for PostgreSQL-backed application state."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for persisted prototype records."""
