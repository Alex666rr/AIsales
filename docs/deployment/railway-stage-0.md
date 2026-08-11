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
set -eu
sh /workspace/infra/postgres/railway/bootstrap_roles.sh
DATABASE_URL="postgresql+psycopg://ai_sales_owner:${POSTGRES_OWNER_PASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}" \
  alembic -c /workspace/alembic.ini upgrade head
```

The command first reconciles the two roles through Railway's managed Postgres
connection. It builds the owner URL only for the Alembic command, runs
`alembic -c /workspace/alembic.ini upgrade head`, and exits successfully when
the migration succeeds. Do not configure a restart loop for this service.

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

Set the API health check path to `/healthz`. A healthy response is the API's
readiness signal after the one-shot migration has completed.

## Valid value construction

Generate `SESSION_ENCRYPTION_KEY` from exactly 32 random bytes and URL-safe
Base64-encode those bytes; do not use a passphrase or a shorter key. Generate
`PLATFORM_OWNER_ID` as UUID version 4. Set `CURRENT_TERMS_REVISION` exactly to
`v1` for the initial deployment. Enter the two Telegram variables in Railway
before intentionally deploying the API.
