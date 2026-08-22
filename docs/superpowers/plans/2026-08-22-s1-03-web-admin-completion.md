# S1.03 Web Administration Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the authenticated administration web application with direct routes, organization profile, team management, generated API contracts and browser-level access checks.

**Architecture:** Extend the existing session-derived tenant boundary with owner-only organization and membership read/deactivation commands. The React app uses the browser pathname as its small router so direct links work without persisting credentials. Typed web client contracts and tests prove owner actions, hidden manager controls and safe UI states.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, React, TypeScript, Vite, Vitest, Playwright.

## Global Constraints

- Every organization and member lookup is scoped to the authenticated session tenant.
- Only `company_owner` may rename an organization, invite staff or disable staff.
- The company owner cannot be disabled or demoted by this UI.
- No password, TOTP secret, recovery code, Telegram session, phone number or token is returned by any endpoint.
- Browser navigation never stores a session token in localStorage.

---

### Task 1: Organization profile and team administration API

**Files:**
- Create: `apps/api/app/modules/organizations/workspace.py`
- Modify: `apps/api/app/modules/organizations/routes.py`
- Modify: `apps/api/app/modules/auth/persistence.py`
- Modify: `apps/api/app/composition.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/modules/organizations/test_routes.py`

**Interfaces:**
- Produces `GET/PATCH /workspace/organization`, `GET /workspace/members`, `POST /workspace/members/{user_id}/deactivate`.
- A profile response contains only `organization_id` and `name`; a member response contains only `user_id`, `email`, `role` and `is_active`.

- [ ] **Step 1: Write API tests** proving tenant-scoped profile retrieval, owner-only rename, member listing and staff deactivation.
- [ ] **Step 2: Run the focused test file** and verify the new routes are absent.
- [ ] **Step 3: Add repository/service/router implementations** that obtain tenant and role solely from the server session.
- [ ] **Step 4: Re-run the focused API test file** and verify it passes.

### Task 2: Direct routes and safe web administration screens

**Files:**
- Create: `apps/web/src/app/router.ts`
- Create: `apps/web/src/features/organizations/organization-profile.tsx`
- Create: `apps/web/src/features/staff/staff-members-list.tsx`
- Modify: `apps/web/src/app/app.tsx`
- Modify: `apps/web/src/shared/api/client.ts`
- Modify: `apps/web/src/styles.css`
- Test: `apps/web/src/app/app.test.tsx`

**Interfaces:**
- Consumes the profile and membership endpoints from Task 1.
- Produces direct `/`, `/accounts`, `/team` navigation with loading, empty, error and permission-denied states.

- [ ] **Step 1: Write failing Vitest cases** for direct `/team`, visible organization name, owner-only controls and hidden manager controls.
- [ ] **Step 2: Run the focused web test file** and verify the expected UI is absent.
- [ ] **Step 3: Implement pathname routing, profile edit and team list** using no client-side secrets and confirmation before disabling a staff member.
- [ ] **Step 4: Re-run the focused web test file** and verify it passes.

### Task 3: Typed client contract and direct-route check

**Files:**
- Modify: `apps/web/src/shared/api/client.ts`
- Test: `apps/web/src/app/app.test.tsx`

**Interfaces:**
- Keeps response contracts explicit at the TypeScript boundary and verifies direct `/`, `/accounts` and `/team` rendering through the existing browser-like test environment.

- [ ] **Step 1: Add direct-route and ownership UI test cases.**
- [ ] **Step 2: Run focused API and web checks, then one production web build.**

### Task 4: Final verification and delivery

- [ ] **Step 1: Run API administration and OpenAPI tests.**
- [ ] **Step 2: Run all web Vitest tests, TypeScript check and Vite build.**
- [ ] **Step 3: Inspect `git diff --check`, commit and push one PR.**
- [ ] **Step 4: Wait for GitHub Actions and fix ordinary CI errors before requesting merge.**
