# AI Sales Manager Stage 3 Quality and Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Завершить MVP через модерируемое накопление опыта, версионирование, A/B-тесты, полную аналитику, контроль расходов и доказанную эксплуатационную устойчивость.

**Architecture:** Stage 3 не меняет границы Stage 1–2, а добавляет event-driven projections, moderation workflows и operational controls поверх стабильных доменных событий. Release принимается только по воспроизводимому отчёту, связывающему 26 критериев MVP с автоматическими тестами и ручными recovery drills.

**Tech Stack:** Existing Stage 0–2 stack, PostgreSQL materialized projections, pgvector, Pytest, Playwright, load-test runner, OpenTelemetry-compatible telemetry, Railway.

## Global Constraints

- Не публиковать знания автоматически.
- Не смешивать статистику разных версий сообщения, профиля, знания или оффера.
- Не отдавать победителю A/B весь трафик без подтверждения администратора.
- Не менять исторические результаты при переименовании или редактировании сущности.
- Не снижать безопасность, tenant isolation, idempotency или recovery criteria ради производительности.
- Не выпускать MVP, пока любой обязательный критерий 1–26 не имеет положительного доказательства.

---

### Task 1: Add Knowledge Candidate Moderation

**Files:**
- Create: `apps/api/app/modules/knowledge/candidates.py`
- Create: `apps/api/app/modules/knowledge/moderation.py`
- Create: `apps/api/tests/modules/knowledge/test_candidates.py`
- Create: `apps/api/tests/modules/knowledge/test_moderation.py`
- Create: `apps/web/src/features/knowledge/candidate-queue.tsx`

**Interfaces:**
- Consumes: AI `KnowledgeCandidateDraft`, source dialogue and current published knowledge.
- Produces: deduplicated candidate and, after human approval, new `KnowledgeVersion`.

- [ ] Write failing tests for extraction without publication, candidate source link, duplicate merge, conflict flag, owner/admin approval, edit, rejection and immutable moderation audit.
- [ ] Implement candidate states `pending, needs_conflict_review, approved, rejected`; retrieval queries must exclude all candidate tables.
- [ ] Implement similarity and exact-hash duplicate search, but require a human decision for every publish.
- [ ] Implement queue UI showing source excerpt, affected current knowledge, diff and approve/edit/reject actions.
- [ ] Run `pytest apps/api/tests/modules/knowledge -q && pnpm --dir apps/web test`; expect PASS.
- [ ] Commit with `git commit -m "feat: add moderated knowledge candidates"`.

### Task 2: Implement Version Propagation and Reproducibility

**Files:**
- Create: `apps/api/app/modules/knowledge/propagation.py`
- Create: `apps/api/app/modules/ai/profile_propagation.py`
- Create: `apps/api/tests/modules/knowledge/test_propagation.py`
- Create: `apps/api/tests/modules/ai/test_profile_propagation.py`
- Create: `apps/web/src/shared/components/version-diff-dialog.tsx`

**Interfaces:**
- Consumes: published knowledge/profile version and optional critical propagation command.
- Produces: new-dialogue default or explicitly audited active-dialogue version update.

- [ ] Write tests proving normal versions affect only new dialogues and active dialogues retain their original version.
- [ ] Write tests for critical update preview, affected-dialogue count, owner/admin confirmation, row-level result and rollback-safe batch execution.
- [ ] Persist exact version references in AI attempts and rendered dialogue timeline.
- [ ] Implement reusable version diff and propagation confirmation UI.
- [ ] Run targeted backend/frontend tests; expect PASS.
- [ ] Commit with `git commit -m "feat: add controlled version propagation"`.

### Task 3: Complete A/B Testing and Adaptive Pace

**Files:**
- Create: `apps/api/app/modules/campaigns/experiments.py`
- Create: `apps/api/app/modules/campaigns/adaptive_pace.py`
- Create: `apps/api/tests/modules/campaigns/test_experiments.py`
- Create: `apps/api/tests/modules/campaigns/test_adaptive_pace.py`
- Create: `apps/web/src/features/campaigns/experiment-panel.tsx`

**Interfaces:**
- Consumes: immutable campaign/message versions and delivery/reply/block/error events.
- Produces: experiment metrics, confidence result, recommendation and bounded pace adjustment.

- [ ] Write tests for deterministic allocation, sample-size display, per-touch reply metrics, version isolation and no automatic 100% winner rollout.
- [ ] Write adaptive-pace tests for configured error/block thresholds, gradual decrease, bounded recovery, pause and administrator override.
- [ ] Implement statistical calculation as a pure function with fixed fixtures and report both effect size and uncertainty.
- [ ] Persist every pace change with input window, old/new rate, rule version and audit actor.
- [ ] Run `pytest apps/api/tests/modules/campaigns/test_experiments.py apps/api/tests/modules/campaigns/test_adaptive_pace.py -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: add experiments and adaptive sending pace"`.

### Task 4: Build Full Analytics, Costs and Payouts

**Files:**
- Create: `apps/api/app/modules/analytics/events.py`
- Create: `apps/api/app/modules/analytics/projections.py`
- Create: `apps/api/app/modules/analytics/costs.py`
- Create: `apps/api/app/db/migrations/versions/0016_analytics_projections.py`
- Create: `apps/api/tests/modules/analytics/test_funnel.py`
- Create: `apps/api/tests/modules/analytics/test_costs.py`
- Create: `apps/web/src/features/analytics/funnel-chart.tsx`
- Create: `apps/web/src/features/analytics/cost-dashboard.tsx`

**Interfaces:**
- Consumes: immutable domain, AI usage, tracking and manager events.
- Produces: tenant-scoped funnel, conversions, operational metrics, AI costs and payout totals.

- [ ] Write projection tests for sent/delivered/errors, reply by touch, ignore/refusal/stop/block, qualification/offer/handoff/success, partner events and manager/AI response time.
- [ ] Prove historical metrics retain campaign, variant, profile and offer version labels after later edits.
- [ ] Implement idempotent projection cursor and replay command; replay must yield byte-equivalent aggregate rows.
- [ ] Implement cost calculation by organization/campaign/provider/model and payout amount/currency/source.
- [ ] Add dashboard filters for period, company, campaign, account, manager, profile, offer and variant.
- [ ] Run `pytest apps/api/tests/modules/analytics -q && pnpm --dir apps/web test`; expect PASS.
- [ ] Commit with `git commit -m "feat: add reproducible analytics and cost control"`.

### Task 5: Prove Performance, Security, Recovery and Observability

**Files:**
- Create: `tests/load/contact_import.py`
- Create: `tests/load/inbox_queries.py`
- Create: `tests/load/outbox_worker.py`
- Create: `tests/resilience/test_restart_recovery.py`
- Create: `tests/resilience/test_provider_failures.py`
- Create: `tests/security/test_upload_attacks.py`
- Create: `tests/security/test_cross_tenant_matrix.py`
- Create: `docs/runbooks/incidents.md`
- Create: `docs/runbooks/backup-restore.md`
- Create: `docs/runbooks/monitoring.md`

**Interfaces:**
- Consumes: release-candidate deployment.
- Produces: measured SLO evidence, security results, restore result and operator runbooks.

- [ ] Load-test 1 000-contact import, filtered inbox, scheduler claims and outbox processing at the MVP target; record p50/p95/p99 and error counts.
- [ ] Require the main list screens to open within 2 seconds under target load on a normal test network; record backend p50/p95/p99 separately to localize every measured exception.
- [ ] Kill API/worker during queued sends and verify restart produces no duplicate business send.
- [ ] Simulate Telegram, AI, Sheets, webhook, STT, search, proxy and database transient failures; verify bounded retry, pause or handoff matches section 14.
- [ ] Run path traversal, archive bomb, invalid MIME, oversized upload, secret logging, session download and cross-tenant security tests.
- [ ] Execute restore of daily and weekly backup formats into an isolated database; compare schema, row counts and selected hashes.
- [ ] Verify correlation IDs, queue age, account/proxy/AI/integration health, backup failure alert and budget alert dashboards.
- [ ] Commit with `git commit -m "test: prove operational resilience"`.

### Task 6: Complete MVP Acceptance and Release Runbook

**Files:**
- Create: `tests/e2e/mvp-acceptance.spec.ts`
- Create: `docs/architecture/mvp-acceptance-report.md`
- Create: `docs/runbooks/mvp-release.md`
- Create: `docs/architecture/stage-3-gate-report.md`

**Interfaces:**
- Consumes: all automated suites, Gate 0–2 reports and Stage 3 drill evidence.
- Produces: one evidence row for every MVP criterion and explicit release decision.

- [ ] Create acceptance report columns: criterion number, requirement IDs, automated test IDs, manual evidence URI, result, reviewer and execution timestamp.
- [ ] Map criteria 1–26 exactly; a criterion with no executable evidence is `failed`, not `not applicable`.
- [ ] Run the full backend suite:

```bash
pytest apps/api/tests services/telegram_connector/tests tests/resilience tests/security -q
```

- [ ] Run frontend and end-to-end suites:

```bash
pnpm --dir apps/web test
pnpm --dir apps/web build
pnpm --dir apps/web exec playwright test
```

- [ ] Run the AI evaluation dataset and compare its hash with the Stage 2 approved dataset; record every added case.
- [ ] Execute one approved-channel pilot and one denied-channel test; denied content must not be loaded by AI.
- [ ] Verify Git working tree is clean, migrations upgrade from an empty database, rollback procedure is documented and production secrets are absent from repository history.
- [ ] Approve `stage-3-gate-report.md` only when all 26 criteria pass; otherwise create a defect linked to the failing evidence and keep release blocked.
- [ ] Commit with `git commit -m "docs: approve ai sales manager mvp"`.
- [ ] After human release approval, tag `v0.1.0-mvp` and push the tag.

## Stage 3 Exit Checklist

- [ ] Knowledge candidates require human moderation and preserve sources.
- [ ] Profile/knowledge propagation is versioned, previewed and audited.
- [ ] Experiment statistics do not mix versions or auto-promote a winner.
- [ ] Adaptive pace is bounded and auditable.
- [ ] Analytics replay is deterministic and tenant-safe.
- [ ] AI costs, payouts and currencies reconcile to source events.
- [ ] Target-load latency, queue and import evidence is recorded.
- [ ] Restart and external-provider failures do not duplicate sends.
- [ ] Security and tenant-isolation suites pass.
- [ ] Backup restore succeeds from external storage.
- [ ] Every MVP criterion 1–26 has passing evidence.
- [ ] Stage 3 Gate report contains explicit human release approval.
