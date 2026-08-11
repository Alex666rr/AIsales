# Railway Stage 0 deployment contract

This guide defines the Railway configuration for Stage 0. It deliberately
creates exactly three services: `Postgres`, a one-shot `Migrations` service,
and the `AIsales` API. The API must never receive the database administrator
or `ai_sales_owner` credentials.

All database URLs in this guide use the `postgresql+psycopg` driver. Do not
substitute a driverless PostgreSQL URL or an `asyncpg` URL.

## Postgres

Create Railway's managed PostgreSQL service and name it `Postgres`. Railway
provides `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD`; other
services reference them with the form `${{Postgres.VARIABLE_NAME}}`.

Create one sealed shared variable, `POSTGRES_RUNTIME_PASSWORD`, with a freshly
generated URL-safe password. It is shared because both `Migrations` and
`AIsales` need the runtime role's password. Do not define the owner password
as a shared variable.

## Migrations

Create a repository-backed service named `Migrations`. It is a one-shot job,
not a long-running API. The repository Dockerfile includes
`/workspace/infra/postgres/railway/bootstrap_roles.sh` and the PostgreSQL
`psql` client needed by the start command below.

In Railway Build Configuration, leave Root Directory at its default `/` and
set `RAILWAY_DOCKERFILE_PATH` to `infra/Dockerfile`; this repository has no
root-level Dockerfile. Set these service variables. Mark the passwords sealed.
The Postgres values are Railway references, not copied credentials.

| Variable | Railway value |
| --- | --- |
| `PGHOST` | `${{Postgres.PGHOST}}` |
| `PGPORT` | `${{Postgres.PGPORT}}` |
| `PGDATABASE` | `${{Postgres.PGDATABASE}}` |
| `PGUSER` | `${{Postgres.PGUSER}}` |
| `PGPASSWORD` | `${{Postgres.PGPASSWORD}}` |
| `RAILWAY_DOCKERFILE_PATH` | `infra/Dockerfile` |
| `POSTGRES_OWNER_PASSWORD` | Enter a fresh URL-safe password in this service only. |
| `POSTGRES_RUNTIME_PASSWORD` | `${{shared.POSTGRES_RUNTIME_PASSWORD}}` |

Use this start command:

```sh
sh -c 'set -eu; sh /workspace/infra/postgres/railway/bootstrap_roles.sh; export DATABASE_URL="postgresql+psycopg://ai_sales_owner:${POSTGRES_OWNER_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"; exec alembic -c /workspace/alembic.ini upgrade head'
```

Railway launches a custom start command in exec form, so the explicit `sh -c`
wrapper is required for `set -eu`, variable expansion, and the scoped
`DATABASE_URL` assignment. The command first reconciles the two roles through Railway's managed Postgres
connection. It builds the owner URL only for the Alembic command, runs
`alembic -c /workspace/alembic.ini upgrade head`, and exits successfully when
the migration succeeds. Do not configure a restart loop for this service.

The repository's `Railway migrations image contract` GitHub Actions workflow
runs the Docker build/runtime contract on every pull request and push. Local
test runs skip that check only when Docker is unavailable.

## AIsales

Create a repository-backed API service named `AIsales`. In Railway Build
Configuration, leave Root Directory at its default `/` and set
`RAILWAY_DOCKERFILE_PATH` to `infra/Dockerfile`; this repository has no
root-level Dockerfile. Configure its runtime database URL with the restricted
role only:

| Variable | Railway value |
| --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://ai_sales_runtime:${{shared.POSTGRES_RUNTIME_PASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}` |
| `SESSION_ENCRYPTION_KEY` | URL-safe Base64 encoding of exactly 32 random bytes. |
| `TELEGRAM_API_ID` | Enter the value from Telegram directly in Railway. |
| `TELEGRAM_API_HASH` | Enter the value from Telegram directly in Railway. |
| `PLATFORM_OWNER_ID` | A generated UUID v4. |
| `PLATFORM_OWNER_TOKEN` | A high-entropy secret. |
| `CURRENT_TERMS_REVISION` | `v1` |
| `ENVIRONMENT` | `prototype` |
| `RAILWAY_DOCKERFILE_PATH` | `infra/Dockerfile` |

`TELEGRAM_API_ID` and `TELEGRAM_API_HASH` are entered by the human in Railway;
never commit them or paste their values into this repository. The API has no
other database URL and no administrative database variables.

Railway injects `PORT` for the API service. Do not set it manually. The image
starts Uvicorn through `sh -c`, passes `${PORT:-8000}` as its port, and uses
`8000` only when running outside Railway without `PORT`.

Set the API health check path to `/healthz`. A healthy response is the API's
readiness signal only when PostgreSQL is reachable and its Alembic revision is
the current application head. Database errors and missing or stale migrations
return an unavailable response.

## Valid value construction

Generate `SESSION_ENCRYPTION_KEY` from exactly 32 random bytes and URL-safe
Base64-encode those bytes; do not use a passphrase or a shorter key. Generate
`PLATFORM_OWNER_ID` as UUID version 4. Generate `PLATFORM_OWNER_TOKEN` from at
least 32 random bytes and keep its encoded value at least 32 characters long.
Set `CURRENT_TERMS_REVISION` exactly to `v1` for the initial deployment. Enter
the two Telegram variables in Railway before intentionally deploying the API.

## First deployment handoff

Do not click Deploy until every item below is complete. The required order is
**Postgres → Migrations → AIsales**; do not start a later service while an
earlier one is unhealthy or unresolved.

1. Enter values from Telegram only in `TELEGRAM_API_ID` and
   `TELEGRAM_API_HASH`, directly in Railway. Do not put Telegram values in a
   repository file, command, log, or variable description.
2. Review all staged Railway service and variable changes against this guide.
   Confirm that `AIsales` has only its runtime `DATABASE_URL` and no owner or
   administrative credentials.
3. Deploy `Postgres` and wait until Railway reports it healthy.
4. Deploy `Migrations`. Require successful output from
   `alembic -c /workspace/alembic.ini upgrade head` before proceeding.
   Stop if role bootstrap fails. Stop if Alembic migration fails.
5. Deploy `AIsales` only after the migration job exits successfully. Require
   its `/healthz` endpoint to report healthy.

Stop immediately if any service log prints a secret, or if a role or migration
error appears. Remove the exposed value from Railway, rotate it, correct the
failure, and repeat the checklist from the affected service; do not deploy the
API until the required preceding service is healthy.
