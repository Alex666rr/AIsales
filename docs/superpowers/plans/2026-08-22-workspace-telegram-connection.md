# Workspace Telegram Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a signed-in `company_owner` start and complete Telegram phone/QR connection attempts for their own organization from the web application.

**Architecture:** Keep the existing bearer-protected Stage 0 and local tdata routes unchanged. Add separate session-authenticated workspace routes which derive actor and organization exclusively from the HttpOnly session; adapters bind attempts to the actor and the finalizer persists the account under the organization.

**Tech Stack:** FastAPI, Pydantic, React/TypeScript, Telethon connector, pytest, Vitest.

## Global Constraints

- Browser clients never receive `PLATFORM_OWNER_TOKEN`, encrypted sessions, or raw tdata.
- Only the `company_owner` role may use workspace connection routes.
- tdata conversion stays local and remains outside the web route set.
- Existing bearer routes retain their current behavior.

---

### Task 1: Session-scoped API boundary

**Files:**
- Modify: `apps/api/app/modules/auth/session_auth.py`
- Modify: `apps/api/app/modules/telegram_connections/service.py`
- Modify: `apps/api/app/modules/telegram_connections/routes.py`
- Modify: `apps/api/app/composition.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/modules/telegram_connections/test_workspace_routes.py`

- [x] Write a failing route test proving a `company_owner` can start an attempt and a non-owner receives 403.
- [x] Implement a session dependency that returns trusted `TenantContext` only for `company_owner`.
- [x] Implement workspace attempt/status services which use `actor_id` for adapter ownership and `organization_id` for account provisioning.
- [x] Mount `/workspace/telegram/connections` separately from bearer and tdata routes.
- [x] Run focused API tests. Full repository verification is delegated to GitHub Actions because the local Codex Python is 3.12 while the project requires Python 3.13.

### Task 2: Web account connection flow

**Files:**
- Modify: `apps/web/src/app/app.tsx`
- Modify: relevant web tests under `apps/web/src`

- [x] Write failing client/component tests for phone/code/2FA and QR states.
- [x] Implement session-cookie requests only to the workspace route set.
- [x] Render redacted errors and never persist phone code, password, QR URL, or session data in browser storage.
- [x] Run focused web tests and production build.

### Task 3: Integration and handoff

- [x] Run backend and web verification once after both tasks.
- [ ] Commit, push the feature branch, and wait for GitHub Actions.
- [ ] Open a PR; do not merge or deploy without owner approval.
