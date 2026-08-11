# Unified Telegram Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать единый безопасный backend-процесс подключения Telegram по телефону/2FA, QR и локальному `tdata`, который сохраняет только зашифрованную серверную сессию.

**Architecture:** Новый модуль `apps/api/app/modules/telegram_connections` владеет короткоживущими owner-bound попытками и API-ответами. Он использует существующие `PhoneAdapter`, `QRAdapter`, `TDataAdapter`, encrypted `SessionStore`, PostgreSQL connection repository и `ConnectionSupervisor`; raw `tdata`, code и password никогда не хранятся в API/БД.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy/PostgreSQL, Telethon boundaries, AES-GCM session store, pytest.

## Global Constraints

- Никаких реальных Telegram credentials, `tdata`, session strings, кодов или 2FA в исходниках, тестах, логах, HTTP errors либо Git.
- Все public routes требуют `OwnerBearerAuthenticator`; owner/organization нельзя получить из request body.
- Любая ошибка persistence/encryption/ownership должна завершаться deny и очисткой transient material.
- Raw `tdata` импортируется только локальным CLI; Railway принимает только одноразовый ticket и encrypted session envelope.
- Каждый production change начинается с RED-теста, затем минимальный GREEN, focused/full tests и отдельный commit.

---

### Task 1: Owner-bound connection-attempt service

**Files:**
- Create: `apps/api/app/modules/telegram_connections/models.py`
- Create: `apps/api/app/modules/telegram_connections/service.py`
- Create: `apps/api/tests/modules/telegram_connections/test_service.py`

**Interfaces:**
- Consumes: `PhoneAdapter.start/submit_code/submit_password`, `QRAdapter.start/complete`, `PlatformOwnerPrincipal`.
- Produces: `ConnectionMethod`, `AttemptStatus`, `ConnectionAttemptService.start_phone/start_qr/submit_code/submit_password/qr_status`.

- [ ] **Step 1: Write failing owner, expiry and state-transition tests.**

```python
async def test_other_owner_cannot_submit_a_phone_code():
    attempt = await service.start_phone(owner_a, "+12025550123")
    result = await service.submit_code(owner_b, attempt.id, "12345")
    assert result.status == AttemptStatus.FAILED
```

- [ ] **Step 2: Run the focused test; expect collection failure because the module is absent.**

Run: `python -m pytest apps/api/tests/modules/telegram_connections/test_service.py -q`

- [ ] **Step 3: Implement bounded in-memory transient attempts.**

```python
class ConnectionAttemptService:
    async def start_phone(self, owner: PlatformOwnerPrincipal, phone: str) -> AttemptView: ...
    async def submit_code(self, owner: PlatformOwnerPrincipal, attempt_id: UUID, code: str) -> AttemptView: ...
```

- [ ] **Step 4: Run focused tests; expect green.**
- [ ] **Step 5: Commit.**

### Task 2: Encrypted finalization and supervisor activation

**Files:**
- Modify: `apps/api/app/modules/telegram_connections/service.py`
- Create: `apps/api/app/modules/telegram_connections/finalizer.py`
- Modify: `services/telegram_connector/postgres_state.py`
- Create: `apps/api/app/db/migrations/versions/<new_revision>.py`
- Modify: `apps/api/tests/modules/telegram_connections/test_service.py`

**Interfaces:**
- Consumes: authorized adapter client boundary, `EncryptedSessionStore`, `SqlAlchemyConnectionRepository`, `ConnectionSupervisor`.
- Produces: `ConnectionFinalizer.persist_and_start(owner, material) -> ConnectedAccountView`.

- [ ] **Step 1: Add RED tests for encryption-before-write, unique Telegram numeric account ownership, duplicate-import idempotency and no supervisor start on storage failure.**

```python
async def test_persistence_failure_does_not_start_supervisor():
    with pytest.raises(ConnectionUnavailable):
        await finalizer.persist_and_start(owner, material)
    assert supervisor.started == []
```

- [ ] **Step 2: Run focused tests; expect failure.**
- [ ] **Step 3: Implement atomic finalize flow; clear adapter/transient secret references in `finally`.**
- [ ] **Step 4: Run focused tests and connector persistence tests; expect green.**
- [ ] **Step 5: Commit.**

### Task 3: HTTP routes for phone and QR

**Files:**
- Create: `apps/api/app/modules/telegram_connections/routes.py`
- Modify: `apps/api/app/composition.py`
- Modify: `apps/api/app/main.py`
- Create: `apps/api/tests/modules/telegram_connections/test_routes.py`

**Interfaces:**
- Consumes: `ConnectionAttemptService`, `OwnerBearerAuthenticator`.
- Produces: the six phone/QR endpoints defined in `2026-08-11-unified-telegram-connection-design.md`.

- [ ] **Step 1: Write RED ASGI tests for 401, 422 redaction, phone/2FA transitions and QR owner isolation.**

```python
def test_validation_error_never_echoes_phone_or_code(client):
    response = client.post("/telegram/connections/phone/start", json={"phone": "secret"})
    assert "secret" not in response.text
```

- [ ] **Step 2: Run routes tests; expect routes absent.**
- [ ] **Step 3: Add request models, fixed safe response models and authenticated router.**
- [ ] **Step 4: Mount router only in `create_app(composition=...)`; run tests green.**
- [ ] **Step 5: Commit.**

### Task 4: Local `tdata` import ticket and completion boundary

**Files:**
- Create: `apps/api/app/modules/telegram_connections/tdata_ticket.py`
- Modify: `apps/api/app/modules/telegram_connections/service.py`
- Modify: `services/telegram_connector/importers/tdata/telethon.py`
- Create: `services/telegram_connector/cli/import_tdata.py`
- Create: `apps/api/tests/modules/telegram_connections/test_tdata_ticket.py`
- Create: `services/telegram_connector/tests/test_tdata_cli.py`

**Interfaces:**
- Consumes: guarded `prepare_tdata_copy`, `parse_tdata`, `to_telethon_string`, authenticated API ticket endpoint.
- Produces: one-time X25519 ticket issue/consume and CLI upload of ticket-public-key-encrypted session envelope only.

- [ ] **Step 1: Write RED tests for replayed/expired ticket, owner mismatch, raw-tdata rejection and cleanup after cancellation.**

```python
async def test_complete_rejects_a_replayed_tdata_ticket():
    await tickets.consume(owner, ticket, envelope)
    with pytest.raises(TicketRejected):
        await tickets.consume(owner, ticket, envelope)
```

- [ ] **Step 2: Run target tests; expect failure.**
- [ ] **Step 3: Implement opaque ticket registry with an in-memory X25519 private key; CLI verifies `get_me`, encrypts envelope with ticket public key, and deletes its temporary snapshot in `finally`.**
- [ ] **Step 4: Run target tests plus existing real-format synthetic parser tests; expect green.**
- [ ] **Step 5: Commit.**

### Task 5: Composition, migration, docs and acceptance checks

**Files:**
- Modify: `apps/api/app/composition.py`
- Modify: `apps/api/app/db/migrations/versions/<new_revision>.py`
- Create: `docs/deployment/telegram-account-connection.md`
- Modify: `apps/api/tests/test_composition.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: production composition that refuses incomplete secret/configuration state and documented local/remote operator flow.

- [ ] **Step 1: Write RED tests for schema constraints, missing configuration fail-closed and router composition.**
- [ ] **Step 2: Run focused migration/composition tests; expect failure.**
- [ ] **Step 3: Add only required persistence fields/constraints and production composition wiring.**
- [ ] **Step 4: Run complete suite, Alembic structural checks, compileall, wheel/import smoke and `git diff --check`.**
- [ ] **Step 5: Commit and create a PR; only then perform an explicit opt-in real Telegram acceptance test.**
