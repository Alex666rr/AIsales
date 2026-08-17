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
    assert composition.REQUIRED_SCHEMA_REVISIONS == frozenset({"0012_staff_lifecycle"})


def test_staff_lifecycle_migration_adds_an_immutable_deactivation_timestamp():
    migration = import_module("app.db.migrations.versions.0012_staff_lifecycle")
    statements: list[str] = []

    class FakeOperation:
        def add_column(self, table_name, column):
            statements.append(f"{table_name}:{column.name}")

        def execute(self, statement):
            statements.append(str(statement))

    original_operation = migration.op
    migration.op = FakeOperation()
    try:
        migration.upgrade()
    finally:
        migration.op = original_operation

    assert "app_users:disabled_at" in statements
    assert "REVOKE DELETE ON TABLE public.app_users FROM PUBLIC" in statements


def test_totp_enrollment_migration_uses_expiring_encrypted_challenges():
    migration = import_module("app.db.migrations.versions.0010_totp_enrollment")

    statements: list[str] = []

    class FakeOperation:
        def create_table(self, name, *_columns, **_kwargs):
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

    assert "auth_totp_enrollments" in statements
    assert "ix_auth_totp_enrollments_expires_at:auth_totp_enrollments:expires_at" in statements
    assert any("REVOKE UPDATE, DELETE ON TABLE auth_totp_enrollments FROM PUBLIC" in value for value in statements)


def test_auth_runtime_access_migration_grants_only_the_required_auth_tables():
    migration = import_module("app.db.migrations.versions.0011_auth_runtime_access")

    statements: list[str] = []

    class FakeOperation:
        def execute(self, statement):
            statements.append(str(statement))

    original_operation = migration.op
    migration.op = FakeOperation()
    try:
        migration.upgrade()
    finally:
        migration.op = original_operation

    for table_name in (
        "organizations",
        "app_users",
        "auth_setup_invitations",
        "auth_totp_enrollments",
        "auth_sessions",
    ):
        assert f"REVOKE ALL ON TABLE public.{table_name} FROM PUBLIC" in statements
        assert (
            f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{table_name} TO ai_sales_runtime"
            in statements
        )
