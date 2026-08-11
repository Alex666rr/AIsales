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

At original completion, `infra/Dockerfile` neither copied
`infra/postgres/railway/bootstrap_roles.sh` into `/workspace` nor installed
the PostgreSQL `psql` client. Fix round 1 resolves that repository prerequisite
without changing Railway or initiating a deployment.

## Fix round 1 evidence

Added `test_migrations_image_includes_bootstrap_dependencies_and_uses_sh`
before changing the image or guide. Ran:

```powershell
& 'C:\Users\admin\Documents\Codex\2026-08-06\AIsales\.worktrees\stage-0-telegram-prototype\.venv\Scripts\python.exe' -m pytest services/telegram_connector/tests/test_railway_bootstrap.py -q
```

Result: `1 failed, 2 passed`. The new test failed as intended because the
Dockerfile did not contain `postgresql-client`.

The minimal fix copies only `infra/postgres/railway`, installs
`postgresql-client`, and runs the bootstrap script with `sh` so it does not
depend on the script's executable bit. The documented Alembic command remains
unchanged.

Verification:

```powershell
& 'C:\Users\admin\Documents\Codex\2026-08-06\AIsales\.worktrees\stage-0-telegram-prototype\.venv\Scripts\python.exe' -m pytest services/telegram_connector/tests/test_railway_bootstrap.py -q --basetemp .pytest-tmp\task2-fix-targeted
& 'C:\Users\admin\Documents\Codex\2026-08-06\AIsales\.worktrees\stage-0-telegram-prototype\.venv\Scripts\python.exe' -m pytest -q --basetemp .pytest-tmp\task2-fix-full
& 'C:\Users\admin\Documents\Codex\2026-08-06\AIsales\.worktrees\stage-0-telegram-prototype\.venv\Scripts\python.exe' -m compileall -q services apps
```

Results: targeted contract test `3 passed in 0.34s`; full relevant suite
`225 passed, 3 skipped in 5.28s`; compileall exited 0; and `git diff --check`
exited 0. Docker is not installed in this execution environment, so an image
build could not be run here.
