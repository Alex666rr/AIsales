import ast
import os
import shutil
import subprocess
import sys
from importlib import import_module
from io import StringIO
from pathlib import Path

import yaml
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_compose_runs_migrations_as_owner_and_api_as_non_owner():
    """Using the database owner in the API would bypass least-privilege migration boundaries."""
    compose = yaml.safe_load(
        (PROJECT_ROOT / "infra" / "docker-compose.prototype.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    owner_password = "${POSTGRES_OWNER_PASSWORD:?POSTGRES_OWNER_PASSWORD is required}"
    runtime_password = "${POSTGRES_RUNTIME_PASSWORD:?POSTGRES_RUNTIME_PASSWORD is required}"

    assert services["db"]["environment"] == {
        "POSTGRES_DB": "ai_sales",
        "POSTGRES_USER": "ai_sales_owner",
        "POSTGRES_PASSWORD": owner_password,
        "APP_DB_PASSWORD": runtime_password,
    }
    assert services["migrate"]["environment"]["DATABASE_URL"] == (
        "postgresql+psycopg://ai_sales_owner:"
        f"{owner_password}@db:5432/ai_sales"
    )
    assert services["api"]["environment"]["DATABASE_URL"] == (
        "postgresql+psycopg://ai_sales_runtime:"
        f"{runtime_password}@db:5432/ai_sales"
    )
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["migrate"]["command"] == ["alembic", "-c", "/workspace/alembic.ini", "upgrade", "head"]


def test_postgres_init_script_keeps_unix_line_endings_on_checkout():
    """Windows Git checkouts must not make the Alpine initialization script unexecutable."""
    attributes = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "infra" / "postgres" / "init" / "001_runtime_role.sh").read_bytes()

    assert "*.sh text eol=lf" in attributes
    assert b"\r\n" not in script


def test_alembic_config_points_at_the_versioned_migration_tree():
    """Without script_location, the deployment command cannot discover or upgrade the schema."""
    config_path = PROJECT_ROOT / "alembic.ini"
    assert config_path.exists(), "alembic.ini is missing"

    config = Config(config_path)
    assert config.get_main_option("script_location") == "apps/api/app/db/migrations"
    assert config.get_main_option("prepend_sys_path") == "."


def test_alembic_environment_is_syntactically_executable():
    """A valid config cannot compensate for a migration environment that fails to import."""
    environment_path = PROJECT_ROOT / "apps" / "api" / "app" / "db" / "migrations" / "env.py"

    ast.parse(environment_path.read_text(encoding="utf-8"), filename=str(environment_path))


def test_telegram_state_migration_renders_schema_and_runtime_privileges():
    """The deployment migration must create every source-of-truth table and explicit runtime grants."""
    migration_path = (
        PROJECT_ROOT
        / "apps"
        / "api"
        / "app"
        / "db"
        / "migrations"
        / "versions"
        / "0002_telegram_state.py"
    )
    assert migration_path.exists(), "telegram state migration is missing"
    module = import_module("apps.api.app.db.migrations.versions.0002_telegram_state")
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql+psycopg://migration.invalid/prototype",
        opts={"as_sql": True, "literal_binds": True, "output_buffer": output},
    )
    module.op = Operations(context)
    module.upgrade()
    sql = output.getvalue()

    for table in (
        "telegram_accounts",
        "telegram_session_ciphertexts",
        "telegram_connections",
        "telegram_proxies",
        "telegram_proxy_overrides",
        "telegram_proxy_assignments",
    ):
        assert f"CREATE TABLE {table}" in sql
        assert f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE" in sql
    assert "TO ai_sales_runtime" in sql
    assert "CREATE UNIQUE INDEX uq_telegram_proxies_one_default" in sql


def test_built_wheel_imports_top_level_connector_outside_source_tree(tmp_path):
    """The built distribution exposes telegram_connector without source-tree imports."""
    source_tree = tmp_path / "source"
    wheel_dir = tmp_path / "wheel"
    install_dir = tmp_path / "installed"
    shutil.copytree(
        PROJECT_ROOT,
        source_tree,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", ".pytest-tmp", "build", "*.egg-info", "__pycache__"
        ),
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-cache-dir",
            "--no-deps",
            "--no-build-isolation",
            "--ignore-requires-python",
            "--wheel-dir",
            str(wheel_dir),
            str(source_tree),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_dir.glob("*.whl"))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--ignore-requires-python",
            "--target",
            str(install_dir),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    environment = {**os.environ, "PYTHONPATH": str(install_dir)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import telegram_connector as connector; "
                "import app.main as api_main; "
                "import app.modules.policy.service as policy_service; "
                "print(connector.__file__); print(connector.SessionAdapter); "
                "print(api_main.__file__); print(policy_service.__file__)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
    )

    connector_file, adapter_symbol, api_file, policy_file = result.stdout.splitlines()
    assert Path(connector_file).resolve().is_relative_to(install_dir.resolve())
    assert "telegram_connector.adapters.base.SessionAdapter" in adapter_symbol
    assert Path(api_file).resolve().is_relative_to(install_dir.resolve())
    assert Path(policy_file).resolve().is_relative_to(install_dir.resolve())
