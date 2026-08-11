from pathlib import Path


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
    assert "GRANT CONNECT, CREATE ON DATABASE" in script
    assert "GRANT CONNECT ON DATABASE" in script


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
