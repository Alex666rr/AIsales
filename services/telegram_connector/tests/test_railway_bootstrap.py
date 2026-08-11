import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_bootstrap_requires_inputs_and_reconciles_roles():
    """A repeat Railway bootstrap must safely reconcile the two least-privilege roles."""
    script_path = PROJECT_ROOT / "infra" / "postgres" / "railway" / "bootstrap_roles.sh"
    assert script_path.exists(), "Railway role bootstrap script is missing"

    script = script_path.read_text(encoding="utf-8")

    for variable in (
        "PGHOST",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
        "POSTGRES_OWNER_PASSWORD",
        "POSTGRES_RUNTIME_PASSWORD",
    ):
        assert f"${{{variable}:?" in script

    assert "ai_sales_owner" in script
    assert "ai_sales_runtime" in script
    assert "IF NOT EXISTS" in script
    assert "ALTER ROLE ai_sales_owner PASSWORD" in script
    assert "ALTER ROLE ai_sales_runtime PASSWORD" in script
    restrictive_attributes = (
        "WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOINHERIT NOREPLICATION NOBYPASSRLS"
    )
    assert f"ALTER ROLE ai_sales_owner {restrictive_attributes}" in script
    assert f"ALTER ROLE ai_sales_runtime {restrictive_attributes}" in script
    assert "GRANT CONNECT, CREATE ON DATABASE" in script
    assert "GRANT CONNECT ON DATABASE" in script
    assert "GRANT USAGE, CREATE ON SCHEMA public TO ai_sales_owner" in script


def test_railway_deployment_guide_keeps_owner_credentials_out_of_api():
    """Giving the owner password to AIsales would defeat the dual-role boundary."""
    guide_path = PROJECT_ROOT / "docs" / "deployment" / "railway-stage-0.md"
    assert guide_path.exists(), "Railway Stage 0 deployment guide is missing"

    guide = guide_path.read_text(encoding="utf-8")
    migrations_section = guide.split("## Migrations", maxsplit=1)[1].split("## AIsales", maxsplit=1)[0]
    api_section = guide.split("## AIsales", maxsplit=1)[1]

    assert "postgresql+psycopg://ai_sales_owner:" in migrations_section
    assert "postgresql+psycopg://ai_sales_runtime:" in api_section
    assert "POSTGRES_OWNER_PASSWORD" not in api_section


def test_migrations_image_includes_bootstrap_dependencies_and_uses_sh():
    """A migration image without psql or the script cannot establish role boundaries."""
    dockerfile = (PROJECT_ROOT / "infra" / "Dockerfile").read_text(encoding="utf-8")
    guide = (PROJECT_ROOT / "docs" / "deployment" / "railway-stage-0.md").read_text(
        encoding="utf-8"
    )

    assert "postgresql-client" in dockerfile
    assert "COPY infra/postgres/railway ./infra/postgres/railway" in dockerfile
    assert "sh /workspace/infra/postgres/railway/bootstrap_roles.sh" in guide


def test_api_image_expands_railway_port_through_an_explicit_shell():
    """Docker exec form alone would pass `${PORT}` literally and ignore Railway's port."""
    dockerfile = (PROJECT_ROOT / "infra" / "Dockerfile").read_text(encoding="utf-8")
    command_line = next(
        line.removeprefix("CMD ")
        for line in dockerfile.splitlines()
        if line.startswith("CMD ")
    )

    command = json.loads(command_line)

    assert command[:2] == ["sh", "-c"]
    assert "exec uvicorn" in command[2]
    assert '--port "${PORT:-8000}"' in command[2]


def test_guide_wraps_migrations_start_command_and_documents_port():
    """Railway's exec-form start command must delegate shell syntax to `sh -c`."""
    guide = (PROJECT_ROOT / "docs" / "deployment" / "railway-stage-0.md").read_text(
        encoding="utf-8"
    )
    migrations_section = guide.split("## Migrations", maxsplit=1)[1].split(
        "## AIsales", maxsplit=1
    )[0]
    api_section = guide.split("## AIsales", maxsplit=1)[1].split(
        "## Valid value construction", maxsplit=1
    )[0]

    assert "sh -c 'set -eu;" in migrations_section
    assert "exec alembic -c /workspace/alembic.ini upgrade head" in migrations_section
    assert "Railway injects `PORT`" in api_section
    assert "`${PORT:-8000}`" in api_section


def test_guide_selects_the_infra_dockerfile_for_each_repository_service():
    """Without an explicit Dockerfile path, Railway would not find this image definition."""
    guide = (PROJECT_ROOT / "docs" / "deployment" / "railway-stage-0.md").read_text(
        encoding="utf-8"
    )
    migrations_section = guide.split("## Migrations", maxsplit=1)[1].split("## AIsales", maxsplit=1)[0]
    api_section = guide.split("## AIsales", maxsplit=1)[1]

    for section in (migrations_section, api_section):
        assert "| `RAILWAY_DOCKERFILE_PATH` | `infra/Dockerfile` |" in section


def test_deployment_guide_requires_a_safe_first_deploy_handoff():
    """A first deployment must stop before an unsafe service reaches production."""
    guide = (PROJECT_ROOT / "docs" / "deployment" / "railway-stage-0.md").read_text(
        encoding="utf-8"
    )
    api_section = guide.split("## AIsales", maxsplit=1)[1].split(
        "## Valid value construction", maxsplit=1
    )[0]

    assert "Do not click Deploy until" in guide
    assert "Postgres → Migrations → AIsales" in guide
    assert not re.search(r"`?TELEGRAM_API_(?:ID|HASH)`?\s*(?:=|:)\s*\S+", guide)
    assert "Review all staged Railway service and variable changes" in guide
    assert "Deploy `Postgres` and wait until Railway reports it healthy" in guide
    assert "alembic -c /workspace/alembic.ini upgrade head" in guide
    assert "migration job exits successfully" in guide
    assert "`/healthz` endpoint to report healthy" in guide
    assert "Stop if role bootstrap fails" in guide
    assert "Stop if Alembic migration fails" in guide
    assert "Stop immediately if any service log prints a secret" in guide
    for variable in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
        assert (
            f"| `{variable}` | Enter the value from Telegram directly in Railway. |"
            in api_section
        )


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker is not installed")
def test_built_migrations_image_contains_bootstrap_script_and_psql(tmp_path):
    """A Docker build must retain the bootstrap inputs needed before any database connection."""
    image_id_path = tmp_path / "image-id"
    image_id = None

    try:
        subprocess.run(
            [
                "docker",
                "build",
                "--iidfile",
                str(image_id_path),
                "--file",
                "infra/Dockerfile",
                ".",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        image_id = image_id_path.read_text(encoding="utf-8").strip()
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                image_id,
                "sh",
                "-ec",
                "test -f /workspace/infra/postgres/railway/bootstrap_roles.sh; command -v psql",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        if image_id:
            subprocess.run(
                ["docker", "image", "rm", "--force", image_id],
                check=False,
                capture_output=True,
                text=True,
            )


def test_ci_requires_the_docker_migrations_image_contract():
    """A local Docker skip must not let pull requests bypass the image contract."""
    workflow_path = PROJECT_ROOT / ".github" / "workflows" / "railway-migrations-image.yml"
    assert workflow_path.exists(), "Docker image contract workflow is missing"

    workflow = workflow_path.read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "actions/setup-python@" in workflow
    assert 'pip install ".[test]"' in workflow
    assert (
        "services/telegram_connector/tests/test_railway_bootstrap.py::"
        "test_built_migrations_image_contains_bootstrap_script_and_psql"
    ) in workflow
    assert "services:" in workflow
    assert "image: postgres:18" in workflow
    assert "test_railway_postgres_integration.py" in workflow
    assert "POSTGRES_OWNER_PASSWORD:" in workflow
    assert "POSTGRES_RUNTIME_PASSWORD:" in workflow
