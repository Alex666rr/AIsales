# Railway dual-role bootstrap design

## Goal

Deploy the Stage 0 API on Railway without giving the running API database-owner privileges. PostgreSQL schema migrations use a dedicated owner role; the API uses a separate restricted runtime role.

## Scope

- Add Railway-specific, idempotent role bootstrap executed by a one-shot migrations service.
- Keep the existing PostgreSQL migration model and the API's `postgresql+psycopg` runtime requirement.
- Define valid Railway variable formats and deployment order.
- Add automated tests for the bootstrap contract and deployment configuration.

Out of scope: changing Telegram business logic, deploying with placeholder Telegram credentials, replacing Railway PostgreSQL, or adding a second production database.

## Components and data flow

1. Railway's managed PostgreSQL starts with its own administrative connection and persistent volume.
2. The `Migrations` service connects through that administrative connection. A repository-owned bootstrap script creates or updates `ai_sales_owner` and `ai_sales_runtime` with passwords supplied only to this one-shot service.
3. The bootstrap script grants the owner role the ability to create the application schema. Alembic then runs with an owner-only `postgresql+psycopg` URL.
4. Alembic grants the runtime role only the table/function permissions needed by the API.
5. The API service receives only a runtime `DATABASE_URL`; it never receives the administrative or owner password.

## Secret and configuration contract

- `SESSION_ENCRYPTION_KEY` is a URL-safe Base64 encoding of exactly 32 random bytes.
- `PLATFORM_OWNER_ID` is a generated UUID.
- `PLATFORM_OWNER_TOKEN` is a high-entropy secret.
- `CURRENT_TERMS_REVISION` is an explicit non-secret value, initially `v1`.
- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` are supplied directly by the owner in Railway, never committed or pasted into project files.
- URLs use `postgresql+psycopg`; `asyncpg` and driverless PostgreSQL URLs are rejected.

## Failure handling

- Bootstrap is idempotent: a repeat updates role passwords and does not fail because roles already exist.
- A migration failure leaves the API undeployed/unhealthy rather than allowing it to run against an unknown schema.
- The API fails closed when any required setting or valid database URL is absent.
- Railway's service changes remain staged until the owner intentionally deploys after entering Telegram credentials.

## Verification

- Unit tests prove the script creates/reconciles roles without leaking passwords and that the API configuration contains no owner credentials.
- Configuration tests verify the migration URL and API URL both use psycopg but distinct roles.
- Existing full test suite remains green.
- A deployment checklist records the manual Railway actions and expected logs.

