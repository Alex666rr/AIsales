# Railway Dockerfile Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure Railway builds API and migrations from `infra/Dockerfile`.

**Architecture:** Root `railway.json` forces Railway's Dockerfile builder and points to `/infra/Dockerfile`. A focused test protects this external Railway configuration requirement.

**Tech Stack:** Railway config-as-code, pytest.

## Global Constraints

- Do not add secrets or alter Telegram/database runtime variables.
- Keep API and migrations start commands service-specific in Railway.
- Keep the custom Dockerfile path exactly `/infra/Dockerfile`.

---

### Task 1: Enforce the Dockerfile builder contract

**Files:**

- Create: `railway.json`
- Modify: `services/telegram_connector/tests/test_railway_bootstrap.py`

**Interfaces:**

- Produces a config with `build.builder == "DOCKERFILE"` and `build.dockerfilePath == "/infra/Dockerfile"`.

- [x] **Step 1: Write a failing contract test**

Add `test_railway_config_forces_the_reviewed_dockerfile_builder` to `services/telegram_connector/tests/test_railway_bootstrap.py`. It reads root `railway.json` with `json.loads` and asserts both literal contract values.

- [x] **Step 2: Run it and observe RED**

Run `C:\\Users\\admin\\Documents\\Codex\\2026-08-06\\AIsales\\.venv\\Scripts\\python.exe -m pytest services/telegram_connector/tests/test_railway_bootstrap.py -q`. It must fail because `railway.json` is absent.

- [x] **Step 3: Implement minimally**

Create `railway.json` with builder `DOCKERFILE` and path `/infra/Dockerfile`.

- [x] **Step 4: Run focused GREEN**

Run the same pytest command; it must pass.

- [x] **Step 5: Run full verification and commit**

Run full pytest, `compileall -q services apps`, and `git diff --check`. Commit configuration and test with message `fix: force railway dockerfile builds`.
