# Stage 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tenant context, transactional outbox, immutable audit records, and safe readiness to the existing Stage 0 API.

**Architecture:** New shared contracts are pure application types. SQLAlchemy repositories write audit and outbox records through the existing async session factory in one transaction. A new Alembic migration follows `0004_telegram_identity`; existing Stage 0 migrations remain unchanged.

**Tech Stack:** Python, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL, Pytest.

## Global Constraints

- Every persisted business record contains `organization_id`.
- Failed transactions persist neither audit nor outbox data.
- Audit records are append-only and must not contain secrets or Telegram session material.
- Focused tests run while developing; one full suite runs before the final commit.

---

### Task 1: Shared tenant and audit/outbox contracts

**Files:**
- Create: `apps/api/app/modules/shared/commands.py`
- Create: `apps/api/app/modules/shared/outbox.py`
- Create: `apps/api/app/modules/audit/models.py`
- Create: `apps/api/app/modules/audit/service.py`
- Test: `apps/api/tests/modules/shared/test_outbox.py`
- Test: `apps/api/tests/modules/audit/test_audit.py`

**Interfaces:**
- Produces `TenantContext(organization_id, actor_id, roles)`, `OutboxMessage`, and `AuditEvent`.
- Consumes only UUIDs, UTC timestamps, and safe JSON metadata.

- [ ] **Step 1: Write failing tests for required tenant context and safe event models.**

```python
def test_outbox_message_rejects_empty_idempotency_key():
    with pytest.raises(ValueError):
        OutboxMessage(organization_id=ORG, topic="contact.imported", idempotency_key="", payload={})

def test_audit_event_redacts_forbidden_metadata_keys():
    event = AuditEvent.create(organization_id=ORG, actor_id=ACTOR, action="account.imported", metadata={"session": "raw"})
    assert "session" not in event.metadata
```

- [ ] **Step 2: Run the focused test files and verify missing imports cause RED.**

Run: `python -m pytest apps/api/tests/modules/shared/test_outbox.py apps/api/tests/modules/audit/test_audit.py -q`

- [ ] **Step 3: Implement immutable models and validation.**

```python
@dataclass(frozen=True, slots=True)
class TenantContext:
    organization_id: UUID
    actor_id: UUID
    roles: frozenset[str]
```

Reject blank event names and idempotency keys; remove secret-named metadata keys rather than serializing them.

- [ ] **Step 4: Re-run the focused files and verify GREEN.**

Run: `python -m pytest apps/api/tests/modules/shared/test_outbox.py apps/api/tests/modules/audit/test_audit.py -q`

### Task 2: Transactional PostgreSQL persistence and migration

**Files:**
- Modify: `apps/api/app/db/migrations/env.py`
- Create: `apps/api/app/db/migrations/versions/0005_stage1_foundation.py`
- Modify: `apps/api/app/modules/shared/outbox.py`
- Modify: `apps/api/app/modules/audit/service.py`
- Test: `apps/api/tests/modules/shared/test_outbox.py`
- Test: `apps/api/tests/modules/audit/test_audit.py`

**Interfaces:**
- Consumes `async_sessionmaker[AsyncSession]`, `TenantContext`, `OutboxMessage`, and `AuditEvent`.
- Produces `SqlAlchemyUnitOfWork` with `enqueue()` and `append()` methods that commit atomically.

- [ ] **Step 1: Write failing transaction tests.**

```python
async def test_rollback_writes_neither_outbox_nor_audit(session_factory):
    async with SqlAlchemyUnitOfWork(session_factory) as work:
        await work.outbox.enqueue(message)
        await work.audit.append(event)
        raise AbortTransaction()
    assert await repository.count() == 0
```

- [ ] **Step 2: Run the focused persistence tests and verify RED.**

Run: `python -m pytest apps/api/tests/modules/shared/test_outbox.py apps/api/tests/modules/audit/test_audit.py -q`

- [ ] **Step 3: Add the migration and repositories.**

The migration creates `outbox_messages` and `audit_events` with UUID primary keys, organization indexes, unique outbox idempotency keys, server timestamps, and restrictive `REVOKE UPDATE, DELETE` statements. The Alembic environment imports the new table metadata. The unit of work commits only on successful context exit and rolls back on exceptions.

- [ ] **Step 4: Run offline migration rendering and focused tests.**

Run: `python -m alembic -c alembic.ini upgrade head --sql`

Run: `python -m pytest apps/api/tests/modules/shared/test_outbox.py apps/api/tests/modules/audit/test_audit.py -q`

### Task 3: Safe readiness endpoint and Stage 0 composition compatibility

**Files:**
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_stage1_health.py`

**Interfaces:**
- Consumes the optional composition object already passed to `create_app`.
- Produces `GET /healthz` with `200` only when database readiness succeeds and a secret-free `503` otherwise.

- [ ] **Step 1: Write failing ASGI tests for ready and unavailable responses.**

```python
def test_healthz_returns_safe_unavailable_on_database_exception():
    status, body = run(asgi_get(create_app(composition=FailingComposition()), "/healthz"))
    assert status == 503
    assert body == {"status": "unavailable"}
```

- [ ] **Step 2: Run the health test and verify RED if the required boundary is absent.**

Run: `python -m pytest apps/api/tests/test_stage1_health.py -q`

- [ ] **Step 3: Make the smallest composition-compatible change required by the test.**

Keep the existing `/healthz` contract for Stage 0 clients. Do not return database errors, URLs, credentials, or session information.

- [ ] **Step 4: Run the focused Stage 1 suite, then one regression suite.**

Run: `python -m pytest apps/api/tests/modules/shared/test_outbox.py apps/api/tests/modules/audit/test_audit.py apps/api/tests/test_stage1_health.py -q`

Run: `python -m pytest -q`

- [ ] **Step 5: Commit the completed foundation.**

```bash
git add apps/api docs/superpowers
git commit -m "feat: add stage 1 data foundation"
```
