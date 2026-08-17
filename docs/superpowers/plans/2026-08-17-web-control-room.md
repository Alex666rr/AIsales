# AIsales Web Control-Room Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the approved dark control-room design to the authenticated AIsales web shell without changing business or security behaviour.

**Architecture:** CSS custom properties in the global stylesheet become the single visual source of truth. Existing React components retain their API calls and state handling; the implementation changes presentation and adds only static operational entry/empty states that make no unsupported claims.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, Testing Library, CSS custom properties.

## Global Constraints

- Keep all API and authentication behaviour unchanged.
- Do not render secrets, raw TOTP URIs, recovery codes or setup tokens beyond current secure flow requirements.
- Meet WCAG AA contrast and preserve keyboard-visible focus.
- Use the tokens from DESIGN.md; do not add a second accent colour.

---

### Task 1: Establish dark theme tokens and primitives

**Files:**
- Modify: apps/web/src/styles.css
- Test: apps/web/src/app/app.test.tsx

**Interfaces:**
- Consumes: DESIGN.md color, radius and spacing tokens.
- Produces: CSS variables and reusable shell, button, input, status and focus styles.

- [ ] **Step 1: Write the failing test**

Add an assertion that the authenticated shell renders navigation, a named workspace and a primary action with their accessible labels.

- [ ] **Step 2: Run test to verify it fails**

Run: pnpm --dir apps/web test

Expected: FAIL because the new visible control has not been rendered.

- [ ] **Step 3: Write minimal implementation**

Define the approved CSS variables on :root. Replace light canvas and white panel literals with canvas, surface, raised-surface, border and text variables. Add :focus-visible rules for buttons, inputs and links.

- [ ] **Step 4: Run test to verify it passes**

Run: pnpm --dir apps/web test

Expected: PASS.

- [ ] **Step 5: Commit**

git add apps/web/src/styles.css apps/web/src/app/app.test.tsx
git commit -m "style: establish dark control room tokens"

### Task 2: Restyle login and secure first-owner setup

**Files:**
- Modify: apps/web/src/features/auth/login-form.tsx
- Modify: apps/web/src/features/auth/setup-wizard.tsx
- Modify: apps/web/src/features/auth/setup-wizard.test.tsx
- Modify: apps/web/src/styles.css

**Interfaces:**
- Consumes: existing login and setup callback props.
- Produces: dark auth surfaces with clear progress, field error and secure TOTP QR presentation.

- [ ] **Step 1: Write the failing test**

Add an assertion that the setup screen exposes its current progress and secure QR description without placing the raw URI in accessible status text.

- [ ] **Step 2: Run test to verify it fails**

Run: pnpm --dir apps/web test

Expected: FAIL because the labelled progress treatment is absent.

- [ ] **Step 3: Write minimal implementation**

Use existing secure setup state, add semantic headings and progress labels, and apply auth-panel styles using only the global tokens.

- [ ] **Step 4: Run test to verify it passes**

Run: pnpm --dir apps/web test

Expected: PASS.

- [ ] **Step 5: Commit**

git add apps/web/src/features/auth apps/web/src/styles.css
git commit -m "style: refine secure authentication surfaces"

### Task 3: Build the owner control-room overview

**Files:**
- Modify: apps/web/src/app/app.tsx
- Modify: apps/web/src/features/staff/staff-invitation-form.tsx
- Modify: apps/web/src/app/app.test.tsx
- Modify: apps/web/src/styles.css

**Interfaces:**
- Consumes: SessionContext and existing StaffInvitationForm behaviour.
- Produces: owner workspace with organisation access context, staff operation and Telegram connection entry state.

- [ ] **Step 1: Write the failing test**

Add assertions that an owner sees Account connection entry and Staff access sections, while a non-owner does not see invitation controls.

- [ ] **Step 2: Run test to verify it fails**

Run: pnpm --dir apps/web test

Expected: FAIL because the operational sections are absent.

- [ ] **Step 3: Write minimal implementation**

Organise the existing workspace into operational sections. Use only factual labels and an explicit no-account entry state; do not invent metrics or activity.

- [ ] **Step 4: Run test to verify it passes**

Run: pnpm --dir apps/web test

Expected: PASS.

- [ ] **Step 5: Commit**

git add apps/web/src/app/app.tsx apps/web/src/features/staff apps/web/src/app/app.test.tsx apps/web/src/styles.css
git commit -m "feat: present owner control room"

### Task 4: Verify the visual and production boundary

**Files:**
- Test: apps/web/src/app/app.test.tsx
- Test: apps/web/src/features/auth/setup-wizard.test.tsx

**Interfaces:**
- Consumes: completed UI tokens and React components.
- Produces: release evidence for the control-room shell.

- [ ] **Step 1: Run complete web test suite**

Run: pnpm --dir apps/web test

Expected: PASS with no failed tests.

- [ ] **Step 2: Build the production client**

Run: pnpm --dir apps/web build

Expected: TypeScript and Vite exit 0.

- [ ] **Step 3: Inspect desktop and mobile once**

Run the Vite client locally, inspect the login, setup and owner views at desktop and narrow widths, fix all discovered issues in one batch, then re-run Steps 1 and 2.

- [ ] **Step 4: Commit**

git add apps/web
git commit -m "test: verify control room web shell"

## Execution record

- 2026-08-17: Tasks 1–3 were completed through RED/GREEN checks and preserved in one focused implementation commit to keep the review surface small.
- 2026-08-17: Complete web test suite and the production Vite build passed locally.
- A browser-rendered visual inspection is deferred to the next authenticated end-to-end session; no unsupported activity or fake Telegram account data has been added to compensate.
