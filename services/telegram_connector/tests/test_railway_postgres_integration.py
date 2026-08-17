"""Real PostgreSQL coverage for Railway role bootstrap and Alembic handoff."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest


psycopg = pytest.importorskip(
    "psycopg",
    reason="psycopg is installed by the Python 3.13 production and CI environments",
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_ENVIRONMENT = (
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "POSTGRES_OWNER_PASSWORD",
    "POSTGRES_RUNTIME_PASSWORD",
)


def _integration_environment_available() -> bool:
    return all(os.environ.get(name) for name in REQUIRED_ENVIRONMENT)


pytestmark = pytest.mark.skipif(
    not _integration_environment_available(),
    reason="Railway PostgreSQL integration environment is not configured",
)


def _database_url(username: str, password: str) -> str:
    host = os.environ["PGHOST"]
    port = os.environ["PGPORT"]
    database = os.environ["PGDATABASE"]
    return (
        f"postgresql+psycopg://{quote(username, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{quote(database, safe='')}"
    )


def test_bootstrap_reconciles_roles_and_owner_migrates_for_restricted_runtime():
    """CI must prove the deployed scripts work against PostgreSQL, not only exist."""
    bootstrap = PROJECT_ROOT / "infra" / "postgres" / "railway" / "bootstrap_roles.sh"
    subprocess.run(["sh", str(bootstrap)], cwd=PROJECT_ROOT, env=os.environ, check=True)

    admin_connection = {
        "host": os.environ["PGHOST"],
        "port": os.environ["PGPORT"],
        "dbname": os.environ["PGDATABASE"],
        "user": os.environ["PGUSER"],
        "password": os.environ["PGPASSWORD"],
        "connect_timeout": 5,
    }
    with psycopg.connect(**admin_connection, autocommit=True) as connection:
        connection.execute(
            "ALTER ROLE ai_sales_owner "
            "SUPERUSER CREATEDB CREATEROLE INHERIT REPLICATION BYPASSRLS"
        )
        connection.execute(
            "ALTER ROLE ai_sales_runtime "
            "SUPERUSER CREATEDB CREATEROLE INHERIT REPLICATION BYPASSRLS"
        )

    subprocess.run(["sh", str(bootstrap)], cwd=PROJECT_ROOT, env=os.environ, check=True)

    owner_url = _database_url("ai_sales_owner", os.environ["POSTGRES_OWNER_PASSWORD"])
    migration_environment = {**os.environ, "DATABASE_URL": owner_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=migration_environment,
        check=True,
    )

    with psycopg.connect(**admin_connection) as connection:
        role_rows = {
            row[0]: tuple(row[1:])
            for row in connection.execute(
                """
                SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                       rolinherit, rolreplication, rolbypassrls
                FROM pg_roles
                WHERE rolname IN ('ai_sales_owner', 'ai_sales_runtime')
                """
            ).fetchall()
        }
        assert role_rows == {
            "ai_sales_owner": (True, False, False, False, False, False, False),
            "ai_sales_runtime": (True, False, False, False, False, False, False),
        }
        assert connection.execute(
            "SELECT has_schema_privilege('ai_sales_owner', 'public', 'CREATE')"
        ).fetchone() == (True,)

    runtime_url = _database_url(
        "ai_sales_runtime", os.environ["POSTGRES_RUNTIME_PASSWORD"]
    ).replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(runtime_url) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0009_global_user_email",
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("CREATE TABLE public.runtime_must_not_create (id integer)")
