"""PostgreSQL migration contract for the Stage 1 data foundation."""

from __future__ import annotations

from importlib import import_module
from io import StringIO

from alembic.migration import MigrationContext
from alembic.operations import Operations


def render_upgrade_sql() -> str:
    """Run the migration through Alembic's PostgreSQL renderer, not a text search."""
    migration = import_module("apps.api.app.db.migrations.versions.0005_stage1_foundation")
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql+psycopg://foundation-test.invalid/prototype",
        opts={"as_sql": True, "literal_binds": True, "output_buffer": output},
    )
    migration.op = Operations(context)
    migration.upgrade()
    return output.getvalue()


def test_foundation_migration_creates_tenant_indexed_append_only_tables():
    """Missing a table, index, or REVOKE would make durable work unauditable or mutable."""
    sql = render_upgrade_sql()

    assert "CREATE TABLE outbox_messages" in sql
    assert "CREATE TABLE audit_events" in sql
    assert "CREATE INDEX ix_outbox_messages_organization_id" in sql
    assert "CREATE INDEX ix_audit_events_organization_id" in sql
    assert "REVOKE UPDATE, DELETE ON TABLE outbox_messages FROM PUBLIC" in sql
    assert "REVOKE UPDATE, DELETE ON TABLE audit_events FROM PUBLIC" in sql
