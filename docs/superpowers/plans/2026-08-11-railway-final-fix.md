# Railway Final Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the final Railway bootstrap review findings without mutating or deploying Railway resources.

**Architecture:** Keep one image for the API and migration job, but make both shell-dependent commands explicitly invoke POSIX `sh`. Harden configuration and readiness at the API boundary, reconcile PostgreSQL roles and schema privileges idempotently, and exercise the complete bootstrap/migration path against PostgreSQL in CI.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL, POSIX shell, Docker, GitHub Actions, pytest.

## Global Constraints

- Use TDD: each behavior test must fail for the expected reason before production changes.
- Do not perform Railway remote mutations or deployments.
- Preserve the dual-role boundary: migrations use `ai_sales_owner`; the API receives only `ai_sales_runtime`.
- Require a URL-safe Base64 encoding of exactly 32 bytes, a UUID v4 owner ID, and an owner token of at least 32 characters.

---

### Task 1: Harden settings and readiness

**Files:**
- Modify: `apps/api/app/config.py`
- Modify: `apps/api/app/composition.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/test_composition.py`
- Modify: `services/telegram_connector/config.py`
- Modify: `services/telegram_connector/tests/test_config.py`

**Interfaces:**
- Consumes: environment-backed `ApiSettings` and production async SQLAlchemy engine.
- Produces: strict secret/identity validation and `/healthz` status that is `200` only when PostgreSQL is reachable at Alembic head `0002_telegram_state`.

- [ ] Add failing settings tests for 16-byte keys, non-v4 UUIDs, and short owner tokens, plus a passing 32-byte URL-safe key case.
- [ ] Add failing ASGI health tests covering unavailable composition, database errors, stale migration revision, and current revision.
- [ ] Implement minimal validators and an async composition readiness query.
- [ ] Run focused tests and confirm green.

### Task 2: Correct process and bootstrap contracts

**Files:**
- Modify: `infra/Dockerfile`
- Modify: `infra/postgres/railway/bootstrap_roles.sh`
- Modify: `docs/deployment/railway-stage-0.md`
- Modify: `services/telegram_connector/tests/test_railway_bootstrap.py`

**Interfaces:**
- Consumes: Railway `PORT`, PostgreSQL admin connection variables, and owner/runtime passwords.
- Produces: a shell-expanded API command, shell-wrapped Railway migration command, reconciled restrictive role attributes, and owner `CREATE` on schema `public`.

- [ ] Add failing contract tests for shell wrapping, `PORT`, role reconciliation, schema privileges, and guide instructions.
- [ ] Update Docker/runtime documentation and bootstrap SQL minimally.
- [ ] Run focused tests and shell syntax checks.

### Task 3: Exercise PostgreSQL bootstrap and migrations in CI

**Files:**
- Modify: `.github/workflows/railway-migrations-image.yml`
- Create: `services/telegram_connector/tests/test_railway_postgres_integration.py`

**Interfaces:**
- Consumes: a real PostgreSQL service and the repository bootstrap/Alembic artifacts.
- Produces: CI evidence that bootstrap is repeatable, roles are restrictive, owner can migrate, runtime cannot create schema objects, and Alembic reaches head.

- [ ] Add an environment-gated integration test and first confirm it cannot yet be invoked by CI contract tests.
- [ ] Add a PostgreSQL service and explicit integration-test invocation to the workflow.
- [ ] Run the integration test locally when Docker is available; otherwise validate workflow parsing and leave the test gated for CI.

### Task 4: Final verification and report

**Files:**
- Create: `final-fix-report.md`

**Interfaces:**
- Consumes: focused red/green evidence and full-suite output.
- Produces: concise final review report with changed contracts, verification commands/results, and confirmation of no Railway remote actions.

- [ ] Run the full pytest suite, compileall, shell syntax check, workflow YAML parse, and `git diff --check`.
- [ ] Write `final-fix-report.md` with exact evidence and remaining environment-dependent notes.
- [ ] Commit the integrated fix without staging unrelated pre-existing files.
