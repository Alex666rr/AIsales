# AI Sales Manager Stage 1 Sales Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать рабочую мультитенантную платформу, которая проводит кампанию от импорта контактов до ручного ответа менеджера без AI, потери данных и повторных отправок.

**Architecture:** Backend организован вертикальными модулями вокруг доменных команд и событий; PostgreSQL хранит бизнес-данные, очередь jobs, outbox и аудит. React-клиент использует сгенерированный из OpenAPI TypeScript client; Telegram connector из Stage 0 подключается только через `TelegramGateway`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL/pgvector, React, TypeScript, Vite, TanStack Query, React Hook Form, Pytest, Vitest, Playwright, Docker, Railway.

## Global Constraints

- Сначала выполнить и принять Stage 0.
- Все запросы и background jobs несут `organization_id`.
- PostgreSQL jobs и transactional outbox используются вместо Redis.
- Любая отправка имеет уникальный idempotency key и проходит стоп-, статус-, расписание-, лимит- и account-health проверки.
- Реальные Telegram-диалоги на Stage 1 обрабатываются менеджером вручную.
- Массовые операции сначала показывают фактическое количество, затем выполняются фоном и формируют построчный отчёт.
- Секреты шифруются и не возвращаются через API после сохранения.

---

### Task 1: Establish API, Database, Outbox and Audit Foundation

**Files:**
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/config.py`
- Modify: `apps/api/app/db/base.py`
- Modify: `apps/api/app/db/session.py`
- Modify: `apps/api/app/db/migrations/env.py`
- Create: `apps/api/app/db/migrations/versions/0002_foundation.py`
- Create: `apps/api/app/modules/shared/commands.py`
- Create: `apps/api/app/modules/shared/outbox.py`
- Create: `apps/api/app/modules/audit/models.py`
- Create: `apps/api/app/modules/audit/service.py`
- Create: `apps/api/tests/test_health.py`
- Create: `apps/api/tests/modules/shared/test_outbox.py`
- Create: `apps/api/tests/modules/audit/test_audit.py`

**Interfaces:**
- Consumes: Stage 0 settings and PostgreSQL.
- Produces: `TenantContext`, `UnitOfWork`, `OutboxRepository`, `AuditWriter`, health endpoints.

- [ ] Write failing tests for health/readiness, tenant context requirement, transactional outbox rollback and append-only audit.
- [ ] Run `pytest apps/api/tests/test_health.py apps/api/tests/modules/shared apps/api/tests/modules/audit -q`; confirm failures reference missing modules.
- [ ] Implement the shared contracts:

```python
@dataclass(frozen=True)
class TenantContext:
    organization_id: UUID
    actor_id: UUID
    roles: frozenset[str]

class UnitOfWork(Protocol):
    outbox: OutboxRepository
    audit: AuditWriter
    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
```

- [ ] Create initial Alembic migration for `outbox_messages` and `audit_events`; deny UPDATE/DELETE through repository methods.
- [ ] Run `alembic upgrade head && pytest apps/api/tests/test_health.py apps/api/tests/modules/shared apps/api/tests/modules/audit -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: add api data and audit foundation"`.

### Task 2: Implement Organizations, Users, RBAC and 2FA

**Files:**
- Create: `apps/api/app/modules/organizations/models.py`
- Create: `apps/api/app/modules/organizations/service.py`
- Create: `apps/api/app/modules/organizations/routes.py`
- Create: `apps/api/app/modules/auth/models.py`
- Create: `apps/api/app/modules/auth/passwords.py`
- Create: `apps/api/app/modules/auth/totp.py`
- Create: `apps/api/app/modules/auth/service.py`
- Create: `apps/api/app/modules/auth/routes.py`
- Create: `apps/api/app/db/migrations/versions/0003_auth_orgs.py`
- Create: `apps/api/tests/modules/auth/test_rbac.py`
- Create: `apps/api/tests/modules/auth/test_2fa.py`
- Create: `apps/api/tests/modules/organizations/test_isolation.py`

**Interfaces:**
- Consumes: `TenantContext`, `AuditWriter`.
- Produces: authenticated web session and `AuthorizationService.require(permission, context)`.

- [ ] Write failing matrix tests for platform owner, company owner, administrator and manager across organization lifecycle, content access, session import, knowledge approval and mass unblocking.
- [ ] Write failing tests for password hashing, mandatory TOTP enrollment, recovery codes, active-session listing and forced logout.
- [ ] Run `pytest apps/api/tests/modules/auth apps/api/tests/modules/organizations -q`; confirm expected failures.
- [ ] Implement permission checks with explicit enum values:

```python
class Permission(StrEnum):
    ORG_ADMIN = "org:admin"
    ACCOUNT_IMPORT = "account:import"
    CAMPAIGN_WRITE = "campaign:write"
    KNOWLEDGE_APPROVE = "knowledge:approve"
    STOP_REMOVE = "stop:remove"
    DIALOG_WRITE = "dialog:write"
```

- [ ] Add database constraints for organization ownership and tests proving cross-tenant IDs return 404 without leaking existence.
- [ ] Run migrations and `pytest apps/api/tests/modules/auth apps/api/tests/modules/organizations -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: add organizations rbac and two factor auth"`.

### Task 3: Build Web Application Shell and Administration

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/src/app/router.tsx`
- Create: `apps/web/src/app/auth-provider.tsx`
- Create: `apps/web/src/shared/api/client.ts`
- Create: `apps/web/src/features/auth/login-page.tsx`
- Create: `apps/web/src/features/auth/two-factor-page.tsx`
- Create: `apps/web/src/features/organizations/organization-page.tsx`
- Create: `apps/web/src/features/users/users-page.tsx`
- Create: `apps/web/tests/auth-flow.test.tsx`
- Create: `tests/e2e/admin-access.spec.ts`
- Create: `packages/contracts/generate-client.mjs`

**Interfaces:**
- Consumes: Stage 1 Task 2 OpenAPI endpoints.
- Produces: typed API client, protected routes and role-aware navigation.

- [ ] Generate the TypeScript client from the committed OpenAPI snapshot and fail CI when regeneration produces a diff.
- [ ] Write failing Vitest cases for login, 2FA challenge, expired session, role-hidden actions and platform-owner audited organization access.
- [ ] Implement `AuthProvider` with states `loading | anonymous | needs_2fa | authenticated`; never persist tokens in localStorage.
- [ ] Implement organization/user screens with explicit loading, empty, error and permission-denied states.
- [ ] Run `pnpm --dir apps/web test && pnpm --dir apps/web build`; expect PASS.
- [ ] Run `pnpm --dir apps/web exec playwright test tests/e2e/admin-access.spec.ts`; expect PASS.
- [ ] Commit with `git commit -m "feat: add authenticated administration console"`.

### Task 4: Productize Telegram Accounts, Bots and Proxies

**Files:**
- Create: `apps/api/app/modules/telegram/models.py`
- Create: `apps/api/app/modules/telegram/service.py`
- Create: `apps/api/app/modules/telegram/routes.py`
- Create: `apps/api/app/modules/proxies/models.py`
- Create: `apps/api/app/modules/proxies/service.py`
- Create: `apps/api/app/modules/proxies/routes.py`
- Create: `apps/api/app/db/migrations/versions/0004_telegram_proxies.py`
- Create: `apps/api/tests/modules/telegram/test_account_lifecycle.py`
- Create: `apps/api/tests/modules/proxies/test_capacity.py`
- Create: `apps/web/src/features/telegram/accounts-page.tsx`
- Create: `apps/web/src/features/proxies/proxies-page.tsx`

**Interfaces:**
- Consumes: Stage 0 adapters, `ConnectionSupervisor`, RBAC and encryption service.
- Produces: account/bot/proxy CRUD, quarantine workflow and normalized health state.

- [ ] Write failing backend tests for owner/admin-only import, quarantine, encrypted session persistence, status transitions, archive deletion of active session, proxy precedence and capacity.
- [ ] Implement account states exactly as `quarantine, active, paused, reauth_required, limited, blocked, archived`.
- [ ] Connect the Stage 0 supervisor through an application service; never expose session downloads or stored secrets.
- [ ] Write frontend tests for phone/QR/import workflows, proxy health and safe error display.
- [ ] Run `pytest apps/api/tests/modules/telegram apps/api/tests/modules/proxies -q && pnpm --dir apps/web test`; expect PASS.
- [ ] Commit with `git commit -m "feat: manage telegram accounts and proxies"`.

### Task 5: Implement Contacts, Identity and Import

**Files:**
- Create: `apps/api/app/modules/contacts/models.py`
- Create: `apps/api/app/modules/contacts/identity.py`
- Create: `apps/api/app/modules/contacts/importer.py`
- Create: `apps/api/app/modules/contacts/routes.py`
- Create: `apps/api/app/db/migrations/versions/0005_contacts.py`
- Create: `apps/api/tests/modules/contacts/test_identity.py`
- Create: `apps/api/tests/modules/contacts/test_import.py`
- Create: `apps/web/src/features/contacts/import-wizard.tsx`
- Create: `apps/web/src/features/contacts/contacts-page.tsx`

**Interfaces:**
- Consumes: CSV/XLSX stream, column map and tenant context.
- Produces: `ImportPreview`, `DuplicateCandidate`, canonical `Contact` and identifier history.

- [ ] Write failing tests for username/phone normalization, Telegram ID priority, exact duplicate merge, ambiguous duplicate review and identifier history.
- [ ] Write failing import tests for 1 000 rows, malformed rows, duplicate counts, stop/block warnings and rollback on invalid mapping.
- [ ] Implement the preview contract:

```python
class ImportPreview(BaseModel):
    valid_count: int
    invalid_rows: list[ImportRowError]
    exact_duplicate_count: int
    ambiguous_duplicate_count: int
    stop_list_count: int
    confirmed_block_count: int
```

- [ ] Implement streaming CSV/XLSX parsing with file-size, row-count and content-type limits; store no original file after completion.
- [ ] Implement the mapping/review UI and tests for all preview counts.
- [ ] Run `pytest apps/api/tests/modules/contacts -q && pnpm --dir apps/web test`; expect PASS.
- [ ] Commit with `git commit -m "feat: add contact import and identity resolution"`.

### Task 6: Add Safe Bulk Operations

**Files:**
- Create: `apps/api/app/modules/contacts/bulk.py`
- Create: `apps/api/app/modules/contacts/bulk_routes.py`
- Create: `apps/api/tests/modules/contacts/test_bulk.py`
- Create: `apps/web/src/shared/components/bulk-action-bar.tsx`
- Create: `apps/web/src/features/contacts/bulk-actions.tsx`
- Create: `tests/e2e/contact-bulk-actions.spec.ts`

**Interfaces:**
- Consumes: explicit IDs or immutable filter snapshot plus exclusions.
- Produces: `BulkPreview`, background `BulkJob` and row-level `BulkResult`.

- [ ] Write failing tests for row/page/all-filter selection, exclusion IDs, changed-filter detection, permission checks, confirmation token and row-level results.
- [ ] Implement `BulkSelection(mode, filter_snapshot, included_ids, excluded_ids)` and require a short-lived confirmation token bound to action and count.
- [ ] Implement PostgreSQL background jobs with progress counters and idempotent row execution.
- [ ] Implement the fixed bulk action bar and confirmation dialog showing the actual count and state breakdown.
- [ ] Run backend, frontend and `contact-bulk-actions.spec.ts`; expect PASS.
- [ ] Commit with `git commit -m "feat: add audited bulk contact operations"`.

### Task 7: Implement Independent Contact Status Dimensions

**Files:**
- Create: `apps/api/app/modules/statuses/models.py`
- Create: `apps/api/app/modules/statuses/transitions.py`
- Create: `apps/api/app/modules/statuses/service.py`
- Create: `apps/api/app/db/migrations/versions/0006_contact_statuses.py`
- Create: `apps/api/tests/modules/statuses/test_independence.py`
- Create: `apps/api/tests/modules/statuses/test_stop_rules.py`
- Create: `apps/web/src/features/contacts/contact-status-panel.tsx`

**Interfaces:**
- Consumes: `StatusTransitionCommand(dimension, from_value, to_value, reason)`.
- Produces: current `ContactStateSnapshot`, campaign-contact result and immutable history.

- [ ] Write the regression test proving a qualified contact can simultaneously be stop-listed and technically blocked without losing qualification.
- [ ] Write tests proving ordinary refusal does not set stop-list, explicit no-contact does, `Игнор` belongs to one campaign and mass unblocking requires owner/admin confirmation.
- [ ] Implement enums and one-dimension transitions:

```python
class StatusDimension(StrEnum):
    SALES_STAGE = "sales_stage"
    CONTACT_PERMISSION = "contact_permission"
    TECHNICAL_STATE = "technical_state"
```

- [ ] Persist old/new value, campaign, account, touch number, reason, comment, actor and timestamp for every transition.
- [ ] Add UI fields and filters for all three dimensions plus campaign result.
- [ ] Run `pytest apps/api/tests/modules/statuses -q && pnpm --dir apps/web test`; expect PASS.
- [ ] Commit with `git commit -m "feat: separate contact status dimensions"`.

### Task 8: Implement Campaigns, Audiences, Chains and Variants

**Files:**
- Create: `apps/api/app/modules/campaigns/models.py`
- Create: `apps/api/app/modules/campaigns/service.py`
- Create: `apps/api/app/modules/campaigns/assignment.py`
- Create: `apps/api/app/modules/campaigns/routes.py`
- Create: `apps/api/app/db/migrations/versions/0007_campaigns.py`
- Create: `apps/api/tests/modules/campaigns/test_campaign_lifecycle.py`
- Create: `apps/api/tests/modules/campaigns/test_assignment.py`
- Create: `apps/web/src/features/campaigns/campaign-wizard.tsx`
- Create: `tests/e2e/campaign-wizard.spec.ts`

**Interfaces:**
- Consumes: audience, chain 1–3, accounts, schedule, variants, limits and handoff rules.
- Produces: immutable `CampaignVersion`, `CampaignContact` and deterministic account assignments.

- [ ] Write failing tests for campaign states, chain length, versioning, one active outbound dialogue, stop/block exclusion and account capacity.
- [ ] Write assignment test for the 50/20/30 requeue example and uncertain-send reconciliation.
- [ ] Implement weighted variant assignment stable by `campaign_contact_id`; variant edits create a new version.
- [ ] Implement the 11-step campaign wizard from section 10.2 with draft persistence and final confirmation.
- [ ] Run backend tests and `campaign-wizard.spec.ts`; expect PASS.
- [ ] Commit with `git commit -m "feat: add versioned campaign workflow"`.

### Task 9: Build Scheduler, Limits and Idempotent Sending

**Files:**
- Create: `apps/api/app/modules/campaigns/scheduler.py`
- Create: `apps/api/app/modules/messaging/models.py`
- Create: `apps/api/app/modules/messaging/sender.py`
- Create: `apps/api/app/modules/messaging/reconciliation.py`
- Create: `apps/api/app/db/migrations/versions/0008_messaging.py`
- Create: `apps/api/tests/modules/messaging/test_scheduler.py`
- Create: `apps/api/tests/modules/messaging/test_idempotency.py`
- Create: `apps/api/tests/modules/messaging/test_requeue.py`

**Interfaces:**
- Consumes: campaign contacts and account/proxy health.
- Produces: due `SendMessageCommand`, exactly-once business effect and delivery attempt history.

- [ ] Write failing tests for campaign timezone, weekdays, quiet hours, 1–3 minute jitter, per-account daily limit, adaptive pause thresholds and incoming-response cancellation of future touches.
- [ ] Write restart and duplicate-job tests using a fixed idempotency key.
- [ ] Implement claim with `SELECT ... FOR UPDATE SKIP LOCKED`; persist attempt before Telegram I/O and reconcile timeout before retry.
- [ ] Requeue unsent contacts from blocked accounts; route lost active dialogues to emergency queue without automatic account transfer.
- [ ] Run `pytest apps/api/tests/modules/messaging -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: schedule idempotent telegram sends"`.

### Task 10: Implement Inbox, Managers, Shifts and Handoff

**Files:**
- Create: `apps/api/app/modules/inbox/models.py`
- Create: `apps/api/app/modules/inbox/assignment.py`
- Create: `apps/api/app/modules/inbox/service.py`
- Create: `apps/api/app/modules/inbox/routes.py`
- Create: `apps/api/app/db/migrations/versions/0009_inbox.py`
- Create: `apps/api/tests/modules/inbox/test_assignment.py`
- Create: `apps/api/tests/modules/inbox/test_handoff.py`
- Create: `apps/web/src/features/inbox/inbox-page.tsx`
- Create: `apps/web/src/features/inbox/dialog-page.tsx`
- Create: `tests/e2e/manual-handoff.spec.ts`

**Interfaces:**
- Consumes: incoming private message, manager shift/status/capacity and handoff reason.
- Produces: shared/personal/emergency queues, assignment and manager-owned dialogue.

- [ ] Write failing tests for shared and round-robin assignment, on-shift/online/capacity eligibility, no-manager urgent queue and emergency dialogue.
- [ ] Write handoff tests proving automatic replies stop, summary/reason are present and only an explicit action returns control.
- [ ] Implement inbox query filters for every status dimension, campaign, account, manager, nullable AI profile field and period.
- [ ] Implement realtime updates through Server-Sent Events with reconnect cursor.
- [ ] Run backend tests and `manual-handoff.spec.ts`; expect PASS.
- [ ] Commit with `git commit -m "feat: add manager inbox and handoff"`.

### Task 11: Add Notifications and Manual Messaging

**Files:**
- Create: `apps/api/app/modules/notifications/models.py`
- Create: `apps/api/app/modules/notifications/service.py`
- Create: `apps/api/app/modules/notifications/channels.py`
- Create: `apps/api/app/db/migrations/versions/0010_notifications.py`
- Create: `apps/api/tests/modules/notifications/test_preferences.py`
- Create: `apps/api/tests/modules/messaging/test_manual_send.py`
- Create: `apps/web/src/features/notifications/notification-center.tsx`

**Interfaces:**
- Consumes: domain events and user notification preferences.
- Produces: web notifications, service-bot notifications and manual `SendMessageCommand`.

- [ ] Write event tests for handoff, negative response, readiness to buy, overdue work, no managers, account/proxy errors, stopped campaign and integration/backup failures.
- [ ] Test user quiet hours and the rule that platform-owner critical technical alerts cannot be fully disabled.
- [ ] Route manager messages through the same stop, permission, schedule override, account health, audit and idempotency pipeline.
- [ ] Run `pytest apps/api/tests/modules/notifications apps/api/tests/modules/messaging/test_manual_send.py -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: add notifications and manual messaging"`.

### Task 12: Implement Sheets, Public API, Webhooks and Baseline Analytics

**Files:**
- Create: `apps/api/app/modules/integrations/sheets.py`
- Create: `apps/api/app/modules/integrations/webhooks.py`
- Create: `apps/api/app/modules/integrations/routes.py`
- Create: `apps/api/app/modules/analytics/queries.py`
- Create: `apps/api/app/modules/analytics/routes.py`
- Create: `apps/api/app/db/migrations/versions/0011_integrations_analytics.py`
- Create: `apps/api/tests/modules/integrations/test_sheets.py`
- Create: `apps/api/tests/modules/integrations/test_webhooks.py`
- Create: `apps/api/tests/modules/analytics/test_baseline.py`
- Create: `apps/web/src/features/integrations/integrations-page.tsx`
- Create: `apps/web/src/features/analytics/dashboard-page.tsx`

**Interfaces:**
- Consumes: domain events and tenant-scoped API credentials.
- Produces: one-way Sheets projection, signed retrying webhooks, export and baseline funnel queries.

- [ ] Write Sheets tests for the required sheets, all independent status fields, five-minute target, retry queue and no reverse synchronization.
- [ ] Write webhook tests for HMAC signature, exponential retry, delivery journal, manual retry and idempotent incoming postback.
- [ ] Write analytics tests for send/reply/ignore/refusal/stop/block/qualification/handoff/success dimensions and stable historical versions.
- [ ] Implement tenant-scoped API keys with hashed storage, expiry and permission scopes.
- [ ] Implement integration health UI and baseline dashboard filters.
- [ ] Run `pytest apps/api/tests/modules/integrations apps/api/tests/modules/analytics -q && pnpm --dir apps/web test`; expect PASS.
- [ ] Commit with `git commit -m "feat: add integrations and baseline analytics"`.

### Task 13: Harden, Deploy and Prove the Manual Campaign

**Files:**
- Create: `infra/railway/railway.toml`
- Modify: `infra/Dockerfile`
- Create: `infra/scripts/backup.ps1`
- Create: `infra/scripts/restore-check.ps1`
- Create: `apps/api/tests/security/test_secret_redaction.py`
- Create: `apps/api/tests/security/test_tenant_isolation.py`
- Create: `tests/e2e/stage1-manual-campaign.spec.ts`
- Create: `docs/runbooks/backup-restore.md`
- Create: `docs/runbooks/manual-campaign-pilot.md`
- Create: `docs/architecture/stage-1-gate-report.md`

**Interfaces:**
- Consumes: all Stage 1 modules.
- Produces: deployable Railway artifact, external backup drill and Gate 1 evidence.

- [ ] Add structured logs with correlation ID from Telegram update/job through API, database and send result; redact all secret fields.
- [ ] Add readiness checks for database, connector, queue age, account/proxy health and integrations.
- [ ] Configure one application service plus PostgreSQL/pgvector, external object storage and budget alerts; do not add Redis.
- [ ] Write the end-to-end pilot: import contacts, preview, campaign approval, scheduled send, incoming response, manual manager reply, status updates, Sheets projection and audit verification.
- [ ] Run `pytest apps/api/tests services/telegram_connector/tests -q`.
- [ ] Run `pnpm --dir apps/web test && pnpm --dir apps/web build && pnpm --dir apps/web exec playwright test tests/e2e/stage1-manual-campaign.spec.ts`.
- [ ] Execute backup and restore scripts against an isolated test database; record timestamps and row-count checks.
- [ ] Complete `docs/architecture/stage-1-gate-report.md`; Gate 1 passes only with zero lost contacts and zero duplicate sends.
- [ ] Commit with `git commit -m "test: prove stage 1 manual campaign"`.

## Stage 1 Exit Checklist

- [ ] Tenant isolation and RBAC matrices pass.
- [ ] 2FA is mandatory for privileged roles.
- [ ] Account/session/proxy secrets are encrypted and redacted.
- [ ] Import handles at least 1 000 contacts without lost rows.
- [ ] Three status dimensions and campaign result remain independent.
- [ ] Stop/block rules prevent sending without audited two-step removal.
- [ ] Scheduler respects chain, timezone, quiet hours, jitter and limits.
- [ ] Restart and uncertain-result tests produce no duplicate message.
- [ ] Blocked-account remainder returns to the pool; active dialogue enters emergency queue.
- [ ] Manager inbox, shifts, assignment and manual replies work end to end.
- [ ] Sheets, API and webhooks expose tenant-safe results.
- [ ] Backup restores successfully in an isolated environment.
- [ ] Stage 1 Gate report is approved before Stage 2 starts.
