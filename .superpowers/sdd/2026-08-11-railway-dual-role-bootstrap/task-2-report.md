# Task 2 report: Railway migration and API contract

## RED evidence

Added `test_railway_deployment_guide_keeps_owner_credentials_out_of_api` before
creating the deployment guide. Ran:

```powershell
& 'C:\Users\admin\Documents\Codex\2026-08-06\AIsales\.worktrees\stage-0-telegram-prototype\.venv\Scripts\python.exe' -m pytest services/telegram_connector/tests/test_railway_bootstrap.py -q
```

Result: `1 failed, 1 passed`. The new test failed as intended with
`AssertionError: Railway Stage 0 deployment guide is missing`.

## GREEN evidence

Created `docs/deployment/railway-stage-0.md` with the exact `Postgres`,
one-shot `Migrations`, and `AIsales` contract. The guide documents the
owner-only Alembic URL, runtime-only API URL, the `/healthz` check, Railway
references, and required value formats.

Ran the required targeted tests with a workspace-local pytest base directory:

```powershell
& 'C:\Users\admin\Documents\Codex\2026-08-06\AIsales\.worktrees\stage-0-telegram-prototype\.venv\Scripts\python.exe' -m pytest services/telegram_connector/tests/test_railway_bootstrap.py services/telegram_connector/tests/test_bootstrap.py -q --basetemp .pytest-tmp\task2
```

Result: `8 passed in 3.22s`. Also ran `git diff --check` before committing;
it returned exit code 0.

## Self-review

- The API guide section has only the `ai_sales_runtime` psycopg URL and no
  owner-password variable.
- The owner password is scoped to `Migrations`; it is not a shared Railway
  variable.
- The runtime password is the only database secret shared by `Migrations` and
  `AIsales`.
- No credentials, tokens, or generated secret values are recorded.
- No Railway service was changed and no deployment was initiated.

## Commit

`3c80361 docs: add railway stage 0 deployment contract`

## Concerns

The current `infra/Dockerfile` neither copies `infra/postgres/railway/bootstrap_roles.sh`
into `/workspace` nor installs the PostgreSQL `psql` client. Consequently, a
Migrations service built from that image cannot yet run the documented
bootstrap command. This task intentionally did not modify the Docker image or
Railway configuration; resolve that prerequisite before an intentional
deployment.
