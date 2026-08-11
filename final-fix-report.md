# Railway bootstrap final fix report

Date: 2026-08-11
Branch: `codex/railway-bootstrap`

## Outcome

The final review findings are addressed without changing any Railway remote resource or starting a deployment.

- The API image starts through explicit `sh -c`, expands Railway's injected `PORT`, and defaults to `8000` outside Railway.
- The documented one-shot Migrations start command is a single `sh -c` command, so strict shell mode, variable expansion, bootstrap, and Alembic execution work under Railway's exec-form launcher.
- Role bootstrap now reconciles both existing roles to `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS`, rotates both passwords, and grants the owner `USAGE, CREATE` on schema `public`.
- API and connector settings accept only URL-safe Base64 that decodes to exactly 32 bytes. API settings additionally require a UUID v4 owner ID and an owner token of at least 32 characters.
- `/healthz` fails closed with HTTP 503 when composition is absent, PostgreSQL cannot be queried, the Alembic table is absent, or the revision is stale. HTTP 200 requires exact head `0003_runtime_health`.
- Migration `0003_runtime_health` grants the runtime role only `SELECT` on `public.alembic_version`, allowing readiness checks without schema mutation rights.
- GitHub Actions now provisions PostgreSQL 18 and runs a real integration test. That test executes bootstrap twice, deliberately makes both roles privileged between runs, verifies reconciliation, runs Alembic as `ai_sales_owner`, checks schema privilege, reads the resulting head as `ai_sales_runtime`, and confirms runtime table creation is denied.
- The deployment guide documents `PORT`, fail-closed health semantics, the strict encryption-key and UUID requirements, and meaningful owner-token construction.

## TDD evidence

Before production changes, the focused tests failed on the missing behavior:

- Six failures for weak settings validation and unconditional health success.
- Three failures for missing role-attribute reconciliation, missing schema `public` create privilege, missing API `PORT` shell expansion, and the unwrapped Railway migration command.
- Three failures for the missing runtime-health migration/head and missing PostgreSQL CI service invocation.

After the minimal implementations, all focused tests passed.

## Verification

- `pytest -q`: 239 passed, 5 skipped.
- `python -m compileall -q services apps`: exit 0.
- PyYAML parse of `.github/workflows/railway-migrations-image.yml`: exit 0.
- `python -m alembic -c alembic.ini heads`: `0003_runtime_health (head)`.
- `git diff --check`: exit 0.

The local host has neither Docker nor a POSIX `sh`, and its reusable test interpreter lacks `psycopg`. Therefore the Docker image contract, shell syntax execution, and real PostgreSQL integration test are environment skips locally. The CI job installs the declared dependencies, supplies `sh`, builds the Docker image, starts PostgreSQL 18 with a health check, and explicitly invokes the real integration test; a skip cannot silently satisfy that invocation because all required environment variables and `psycopg` are present there.

## Remote-action boundary

No Railway CLI/API mutation, deploy, service creation, environment update, or other remote action was performed.
