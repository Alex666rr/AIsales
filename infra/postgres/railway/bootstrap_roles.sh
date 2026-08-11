#!/bin/sh
set -eu

: "${PGHOST:?PGHOST is required}"
: "${PGPORT:?PGPORT is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${POSTGRES_OWNER_PASSWORD:?POSTGRES_OWNER_PASSWORD is required}"
: "${POSTGRES_RUNTIME_PASSWORD:?POSTGRES_RUNTIME_PASSWORD is required}"

psql --set=ON_ERROR_STOP=1 \
  --host "$PGHOST" \
  --port "$PGPORT" \
  --username "$PGUSER" \
  --dbname "$PGDATABASE" \
  --set=database_name="$PGDATABASE" \
  --set=owner_password="$POSTGRES_OWNER_PASSWORD" \
  --set=runtime_password="$POSTGRES_RUNTIME_PASSWORD" <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ai_sales_owner') THEN
    CREATE ROLE ai_sales_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
  END IF;

  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ai_sales_runtime') THEN
    CREATE ROLE ai_sales_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
  END IF;
END
$$;

ALTER ROLE ai_sales_owner PASSWORD :'owner_password';
ALTER ROLE ai_sales_runtime PASSWORD :'runtime_password';

GRANT CONNECT, CREATE ON DATABASE :"database_name" TO ai_sales_owner;
GRANT CONNECT ON DATABASE :"database_name" TO ai_sales_runtime;
SQL
