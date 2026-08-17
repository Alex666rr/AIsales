# Stage 1: Foundation Design

## Goal

Add a tenant-safe backend foundation for the sales platform without changing the
working Stage 0 Telegram connection flow. The foundation supplies organization
context, append-only audit events, and a transactional outbox for later
background work.

## Scope

- `TenantContext` carries `organization_id`, `actor_id`, and immutable roles.
- A unit of work writes the domain change, audit event, and outbox message in
  one database transaction.
- Audit events are append-only and always record tenant, actor, action,
  resource, timestamp, and safe metadata.
- Outbox records are tenant-scoped, idempotent, and are not published when the
  enclosing transaction rolls back.
- Health exposes liveness and database readiness without returning secrets.

## Boundaries

Existing Stage 0 PostgreSQL tables, session encryption, policy gate, and
Telegram routes remain unchanged. New tables are introduced only through a new
Alembic migration following the current migration head; no historical migration
is edited.

Repository and service APIs require `TenantContext` before reading or writing
tenant data. Missing or malformed context fails closed. Cross-tenant resources
are represented as not found rather than exposing their existence.

## Data Flow

1. An authenticated request resolves a `TenantContext`.
2. The application service opens a unit of work.
3. It performs its domain write, appends an audit event, and enqueues an outbox
   message with the same transaction.
4. Commit makes all three durable; rollback makes none durable.
5. A later worker consumes the outbox using its idempotency key.

## Error Handling and Security

- Database readiness failures return a safe unavailable result.
- No audit payload or outbox payload may include credentials, session material,
  API keys, phone-login codes, or raw tdata.
- Repository APIs do not provide update or delete operations for audit events.
- All timestamps are database-authoritative where persistence semantics require
  them.

## Verification

Tests are written first and prove:

- readiness is safe when the database is unavailable;
- tenant context is required;
- rollback leaves neither audit nor outbox records;
- committed work persists both records;
- audit records cannot be altered through the application repository.

The focused Task 1 suite runs while developing. One full regression suite runs
before the Task 1 commit and push.
