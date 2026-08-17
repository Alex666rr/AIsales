# Stage 1 TOTP Enrollment and Recovery Codes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete first-owner TOTP enrollment and one-time recovery-code issuance without weakening setup-token, encryption, or server-session boundaries.

**Architecture:** A database-backed enrollment challenge binds a user to a short-lived opaque token and an AES-GCM encrypted TOTP secret. Setup activation creates the challenge; TOTP confirmation consumes it atomically, persists the encrypted secret on the user and returns recovery codes exactly once. Existing `AuthService` continues to issue privileged sessions only after TOTP or a valid remaining recovery code.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL, AES-GCM, Pytest.

## Global Constraints

- No raw setup token, enrollment token, TOTP secret, URI, password or recovery code is persisted, logged or represented after its one permitted response.
- Privileged roles never receive a server session before enrollment confirmation.
- PostgreSQL remains schema-authoritative; migration checks and CI expect the exact current revision.
- Every behavior change starts with a focused failing test; full pytest runs once before commit.

### Task 1: Persist TOTP enrollment challenges

**Files:**
- Modify: `apps/api/app/modules/auth/models.py`
- Modify: `apps/api/app/modules/auth/persistence.py`
- Create: `apps/api/app/db/migrations/versions/0010_totp_enrollment.py`
- Test: `apps/api/tests/modules/auth/test_2fa.py`

**Interfaces:**
- Produces `TotpEnrollmentChallenge(id, user_id, token_hash, encrypted_secret, expires_at, consumed_at)`.
- Produces `SqlAlchemyAuthRepository.create_totp_enrollment(...)` and atomic `consume_totp_enrollment(...)`.

- [x] Write a failing test that setup activation creates one encrypted, expiring challenge whose token is only stored as a hash.
- [x] Run `pytest apps/api/tests/modules/auth/test_2fa.py -q` and confirm RED.
- [x] Add table, model and PostgreSQL migration; implement transaction-backed repository methods.
- [x] Run the focused test and confirm GREEN.

### Task 2: Start and confirm enrollment

**Files:**
- Modify: `apps/api/app/modules/organizations/provisioning.py`
- Modify: `apps/api/app/modules/auth/service.py`
- Modify: `apps/api/app/modules/auth/totp.py`
- Test: `apps/api/tests/modules/auth/test_2fa.py`

**Interfaces:**
- `ProvisioningService.activate_setup_token(...) -> PendingTotpEnrollment` creates an opaque enrollment token and one-time TOTP URI.
- `AuthService.confirm_totp_enrollment(enrollment_token, code) -> RecoveryCodeBundle` atomically consumes a valid challenge.

- [x] Write failing tests for confirmed enrollment, wrong code, expired token and one-time consumption.
- [x] Run focused tests and confirm RED.
- [x] Implement URI construction, six-digit verification, token parsing, encrypted secret persistence and hashed recovery-code generation.
- [x] Run focused tests and confirm GREEN.

### Task 3: Expose safe HTTP responses and production composition

**Files:**
- Modify: `apps/api/app/modules/auth/routes.py`
- Modify: `apps/api/app/composition.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/modules/auth/test_routes.py`
- Test: `apps/api/tests/test_composition.py`

**Interfaces:**
- `POST /auth/setup` returns only `enrollment_token` and `totp_uri` on success.
- `POST /auth/totp/confirm` accepts token/code and returns raw recovery codes only once.

- [x] Write failing ASGI tests for successful safe responses and uniform failure redaction.
- [x] Run focused tests and confirm RED.
- [x] Implement request/response models, routes and composition wiring.
- [x] Run focused tests and confirm GREEN.

### Task 4: Verify and document

**Files:**
- Modify: `docs/runbooks/first-owner-provisioning.md`
- Modify: `apps/api/tests/modules/auth/test_schema.py`
- Modify: `apps/api/tests/test_composition.py`
- Modify: `services/telegram_connector/tests/test_railway_postgres_integration.py`

- [x] Update operator instructions for scanning the TOTP URI and storing recovery codes offline.
- [x] Run auth/organization/composition tests, compileall, Alembic SQL render, full pytest and `git diff --check`.
- [x] Commit only TOTP-enrollment files, push `codex/stage1-foundation`, and confirm the GitHub Actions run is green.
