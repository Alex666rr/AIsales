import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_compose_uses_required_postgres_password_variable():
    """The API and PostgreSQL share one required, non-literal password value."""
    compose = yaml.safe_load(
        (PROJECT_ROOT / "infra" / "docker-compose.prototype.yml").read_text(encoding="utf-8")
    )
    required_password = "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

    assert compose["services"]["api"]["environment"]["DATABASE_URL"] == (
        "postgresql+asyncpg://postgres:"
        "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}@db:5432/ai_sales"
    )
    assert compose["services"]["db"]["environment"]["POSTGRES_PASSWORD"] == required_password
    assert "postgres" not in compose["services"]["db"]["environment"]["POSTGRES_PASSWORD"]


def test_built_wheel_imports_top_level_connector_outside_source_tree(tmp_path):
    """The built distribution exposes telegram_connector without source-tree imports."""
    source_tree = tmp_path / "source"
    wheel_dir = tmp_path / "wheel"
    install_dir = tmp_path / "installed"
    shutil.copytree(
        PROJECT_ROOT,
        source_tree,
        ignore=shutil.ignore_patterns(".git", ".venv", "build", "*.egg-info", "__pycache__"),
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
