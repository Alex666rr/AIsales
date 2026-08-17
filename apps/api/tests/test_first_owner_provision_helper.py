from __future__ import annotations

from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = PROJECT_ROOT / "tools" / "provision_first_owner.ps1"


def test_helper_rejects_an_insecure_base_url_before_prompting_for_a_token() -> None:
    """A mistaken HTTP endpoint must never receive the platform owner token."""

    completed = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(HELPER),
            "-BaseUrl",
            "http://example.test",
            "-OrganizationName",
            "Example",
            "-OwnerEmail",
            "owner@example.test",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode != 0
    assert "HTTPS" in (completed.stdout + completed.stderr)
    assert "PLATFORM_OWNER_TOKEN" not in (completed.stdout + completed.stderr)
