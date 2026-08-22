# AIsales Presentation Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a real, controlled end-to-end sales workflow for an internal presentation: account connection through manual reply and baseline statistics.

**Architecture:** Extend the existing tenant-scoped FastAPI, PostgreSQL and React control room in vertical slices. Every action uses the existing session-authenticated tenant context, audit/outbox boundary and Telegram gateway; no browser mock state or direct Telegram call is allowed. The presentation release narrows scale and user-facing complexity but does not introduce a disposable demo architecture.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Telethon connector, React, TypeScript, Vite, Vitest, Playwright, Pytest, Railway.

## Global Constraints

- Only project-owned test Telegram accounts and consenting test contacts are used until the full Stage 1 gate passes.
- Every mutable business entity is scoped by `organization_id`; every critical action is audited.
- Telegram sessions, API credentials, proxy passwords and recovery codes are never returned by API responses or rendered in the web interface.
- `TelegramGateway` and `OutboxRepository` remain the sole paths to Telegram sending; retries reconcile before any repeat send.
- The first campaign permits at most three contacts and one touch per contact; limits are enforced on the backend.
- AI cannot load or send real Telegram content in this release.
- The product UI follows the approved dark control-room style and uses preflight, clear states and task history rather than isolated “modules”.

---

### Task 0: Make Trello status updates lightweight

**Files:**
- Modify: `tools/trello/sync_board.py`
- Modify: `tools/trello/test_sync_board.py`
- Modify: `docs/runbooks/trello-project-board.md`

**Interfaces:**
- Consumes: existing card IDs and board credentials from ignored `.env`.
- Produces: a synchronizer that updates card content without moving an existing card; an explicit command changes status when requested.

- [ ] Write a failing test proving an existing `Готово` card remains in its list after structural synchronization.
- [ ] Add `--set-status CARD_ID=LIST_NAME` parsing and call the existing Trello move operation only for that explicit argument.
- [ ] Change the normal sync path to create missing cards/lists and refresh descriptions only.
- [ ] Document one command for routine completion; do not create a feature branch or run GitHub CI for an ordinary Trello move.
- [ ] Run `pytest tools/trello/test_sync_board.py -q` and commit `fix: preserve live Trello statuses`.

### Task 1: Finish the account and proxy operational surface (S1.04)

**Files:**
- Modify: `apps/api/app/modules/telegram_connections/models.py`
- Modify: `apps/api/app/modules/telegram_connections/service.py`
- Modify: `apps/api/app/modules/telegram_connections/routes.py`
- Create: `apps/api/app/modules/proxies/models.py`
- Create: `apps/api/app/modules/proxies/service.py`
- Create: `apps/api/app/modules/proxies/routes.py`
- Create: `apps/api/app/db/migrations/versions/0013_telegram_proxy_workspace.py`
- Create: `apps/api/tests/modules/proxies/test_workspace_proxy_health.py`
- Modify: `apps/api/tests/modules/telegram_connections/test_routes.py`
- Modify: `apps/web/src/features/telegram/telegram-accounts-list.tsx`
- Create: `apps/web/src/features/proxies/proxies-page.tsx`
- Modify: `apps/web/src/shared/api/client.ts`

**Interfaces:**
- Consumes: Stage 0 connection records, encrypted session store and tenant session context.
- Produces: redacted `AccountOperationalView`, `ProxyView` and owner-only proxy/account operations.

- [ ] Write backend tests for tenant isolation, no secret fields in account/proxy responses, allowed account state transitions and proxy capacity/health rules.
- [ ] Add normalized account view fields: redacted label, state, state reason, last activity time, assigned proxy health and current task count.
- [ ] Add proxy CRUD limited to owner/admin, encrypt proxy credentials at rest and return only proxy label, protocol, health and assignment count.
- [ ] Add a preflight response that blocks campaign use of quarantined, paused, reauthorization-required, limited, blocked or archived accounts.
- [ ] Build `/accounts` and `/proxies` views with status chips, empty/error states, connect entry point, pause/resume/archive confirmation and no session/phone display.
- [ ] Run focused API and web tests; commit `feat: add operational Telegram account and proxy workspace`.

### Task 2: Add test-contact import and independent statuses (S1.05, S1.07)

**Files:**
- Create: `apps/api/app/modules/contacts/models.py`
- Create: `apps/api/app/modules/contacts/importer.py`
- Create: `apps/api/app/modules/contacts/service.py`
- Create: `apps/api/app/modules/contacts/routes.py`
- Create: `apps/api/app/modules/statuses/service.py`
- Create: `apps/api/app/db/migrations/versions/0014_contacts_statuses.py`
- Create: `apps/api/tests/modules/contacts/test_import_preview.py`
- Create: `apps/api/tests/modules/statuses/test_independent_dimensions.py`
- Create: `apps/web/src/features/contacts/contacts-page.tsx`
- Create: `apps/web/src/features/contacts/import-wizard.tsx`

**Interfaces:**
- Consumes: CSV/XLSX stream, tenant context and audited actor.
- Produces: `ImportPreview`, canonical `Contact`, three independent state dimensions and immutable status history.

- [ ] Write tests for normalized phone/username identity, invalid rows, duplicate detection, stop/blocked warnings and no stored original upload after completion.
- [ ] Implement preview/confirm import with a hard three-contact presentation cap in the first campaign, not in general contact storage.
- [ ] Model `sales_stage`, `contact_permission`, `technical_state` independently and record actor/time/reason on every transition.
- [ ] Build contact list, import review and status history UI with the preflight warnings visible before import confirmation.
- [ ] Run focused API/web tests; commit `feat: import contacts with independent statuses`.

### Task 3: Add selected-audience confirmation (narrow S1.06)

**Files:**
- Create: `apps/api/app/modules/contacts/bulk.py`
- Create: `apps/api/tests/modules/contacts/test_selection_confirmation.py`
- Create: `apps/web/src/shared/components/bulk-action-bar.tsx`
- Modify: `apps/web/src/features/contacts/contacts-page.tsx`

**Interfaces:**
- Consumes: explicit contact IDs and immutable selection snapshot.
- Produces: short-lived `AudienceSelectionToken` bound to organization, actor, count and filtered IDs.

- [ ] Write tests proving selection belongs to one tenant, expires, changes when filters change and cannot be replayed by another actor.
- [ ] Implement preview that shows selected count and state breakdown; require the token when campaign creation consumes a selection.
- [ ] Add a fixed action bar with “Create campaign” and a confirmation screen; no destructive or send action exists in bulk controls.
- [ ] Run targeted tests; commit `feat: add confirmed campaign audience selection`.

### Task 4: Create a controlled campaign and durable send path (S1.08, S1.09)

**Files:**
- Create: `apps/api/app/modules/campaigns/models.py`
- Create: `apps/api/app/modules/campaigns/service.py`
- Create: `apps/api/app/modules/campaigns/routes.py`
- Create: `apps/api/app/modules/messaging/outbox.py`
- Create: `apps/api/app/modules/messaging/sender.py`
- Create: `apps/api/app/db/migrations/versions/0015_campaigns_messaging.py`
- Create: `apps/api/tests/modules/campaigns/test_presentation_campaign.py`
- Create: `apps/api/tests/modules/messaging/test_idempotent_send.py`
- Create: `apps/web/src/features/campaigns/campaign-wizard.tsx`

**Interfaces:**
- Consumes: audience selection token, contact states, account/proxy health and `TelegramGateway`.
- Produces: immutable `CampaignVersion`, `CampaignContact`, `SendMessageCommand` and persisted delivery outcomes.

- [ ] Write tests for preflight failure reasons, one-message chain, three-contact cap, one-touch cap, backend limit enforcement, version immutability and account capacity.
- [ ] Persist an outbox delivery attempt before gateway I/O; give every campaign-contact touch a deterministic idempotency key.
- [ ] On success/failure/uncertain timeout, persist an outcome and expose a safe job/delivery status without Telegram message secrets.
- [ ] Build the campaign wizard: audience → message → account/limit → preflight → explicit launch confirmation → live result.
- [ ] Run targeted API/web tests and one connector integration test; commit `feat: add controlled presentation campaign sending`.

### Task 5: Add incoming reply, manager inbox and manual reply (S1.10)

**Files:**
- Create: `apps/api/app/modules/inbox/models.py`
- Create: `apps/api/app/modules/inbox/service.py`
- Create: `apps/api/app/modules/inbox/routes.py`
- Create: `apps/api/app/db/migrations/versions/0016_inbox.py`
- Create: `apps/api/tests/modules/inbox/test_reply_handoff.py`
- Create: `apps/web/src/features/inbox/inbox-page.tsx`
- Create: `apps/web/src/features/inbox/dialog-page.tsx`

**Interfaces:**
- Consumes: authenticated Telegram inbound event, contact/campaign identifiers and manager context.
- Produces: tenant-scoped inbox item, explicit assignment, manual `SendMessageCommand` and audit event.

- [ ] Write tests proving the reply creates one inbox item, cancels future campaign touches, cannot cross tenants and cannot be auto-transferred to another Telegram account.
- [ ] Implement shared queue, owner/manager assignment and manual reply through the same outbox/gateway command path.
- [ ] Build split inbox/dialog view with filters for unread, assigned and account/campaign; show explicit assignee and recent events.
- [ ] Run focused tests and connector round-trip on project test accounts; commit `feat: add manual inbox handoff and reply`.

### Task 6: Add presentation control room, jobs and baseline statistics (narrow S1.11/S1.12)

**Files:**
- Create: `apps/api/app/modules/analytics/routes.py`
- Create: `apps/api/app/modules/analytics/queries.py`
- Create: `apps/api/app/modules/notifications/service.py`
- Create: `apps/api/tests/modules/analytics/test_presentation_dashboard.py`
- Create: `apps/web/src/features/analytics/dashboard-page.tsx`
- Modify: `apps/web/src/app/app.tsx`
- Modify: `apps/web/src/app/styles.css`

**Interfaces:**
- Consumes: durable account, import, campaign, delivery and inbox events.
- Produces: period-filtered `PresentationDashboardView`, recent job feed and internal critical notification feed.

- [ ] Write tests for organization-scoped counter aggregation and empty-period responses.
- [ ] Show account health, active/failed jobs, imported contacts, queued/sent/failed messages, replies and handoffs.
- [ ] Add internal notifications for new reply, account/proxy failure and failed delivery; user may dismiss ordinary notices but not silently delete audit events.
- [ ] Keep the control room action-oriented: display one next safe action for each empty/problem state.
- [ ] Run focused API/web tests; commit `feat: add presentation dashboard and job visibility`.

### Task 7: Rehearse, document and prove the release

**Files:**
- Create: `tests/e2e/presentation-real-flow.spec.ts`
- Create: `docs/runbooks/presentation-demo.md`
- Create: `docs/architecture/presentation-release-report.md`
- Modify: `tools/trello/sync_board.py` only if a new presentation card is required.

**Interfaces:**
- Consumes: all previous task interfaces and project-owned test accounts.
- Produces: reproducible presentation sequence and evidence of each real result.

- [ ] Run: sign in → verify account → import three test contacts → create/launch campaign → verify one send → reply from recipient → assign and manually reply → verify counters.
- [ ] Verify no duplicate send after a controlled retry/restart test and no secret in browser/API responses.
- [ ] Write a one-page presenter runbook with exact screen order, expected states, recovery actions and prohibited actions.
- [ ] Record deploy version, test result, known presentation constraints and rollback path in the release report.
- [ ] Run targeted suites, production smoke check and commit `test: prove presentation release flow`.

## Plan self-review

- Scope covers the approved real flow and maps each included S1.04–S1.10 and narrow S1.11/S1.12 area to a deliverable.
- Stage 2/3 features remain excluded; no task introduces autonomous AI messaging or high-volume operations.
- Each task has a bounded backend/web interface, tests and a separately reviewable commit.
- The live Trello rule is independent of product CI, preventing status-only work from consuming a delivery cycle.
