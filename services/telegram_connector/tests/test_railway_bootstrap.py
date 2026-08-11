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
