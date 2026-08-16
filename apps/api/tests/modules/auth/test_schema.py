"""Stage 1 access schema migration contracts."""

from __future__ import annotations

from importlib import import_module

from app import composition


def test_access_migration_creates_tenant_scoped_users_and_revocable_sessions():
    migration = import_module("app.db.migrations.versions.0006_stage1_access")

    statements: list[str] = []

    class FakeOperation:
        def create_table(self, name, *columns, **_kwargs):
            statements.append(name)

        def create_index(self, name, table_name, columns):
            statements.append(f"{name}:{table_name}:{','.join(columns)}")

        def execute(self, statement):
            statements.append(str(statement))

    original_operation = migration.op
    migration.op = FakeOperation()
    try:
        migration.upgrade()
    finally:
        migration.op = original_operation

    assert {"organizations", "app_users", "auth_sessions"} <= set(statements)
    assert "ix_app_users_organization_id:app_users:organization_id" in statements
    assert "ix_auth_sessions_user_id:auth_sessions:user_id" in statements
    assert any("REVOKE UPDATE, DELETE ON TABLE auth_sessions FROM PUBLIC" in value for value in statements)


def test_api_readiness_requires_the_access_schema_revision():
    assert composition.REQUIRED_SCHEMA_REVISIONS == frozenset({"0006_stage1_access"})
