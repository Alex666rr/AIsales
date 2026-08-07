# AI Sales Manager Stage 2 AI Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить управляемый AI-диалог с профилями, знаниями, памятью, офферами, поиском, голосовыми сообщениями и безопасной передачей менеджеру.

**Architecture:** AI orchestration является чистым application module: получает подготовленный контекст, вызывает сменного provider adapter и возвращает структурированный `AiDecision`. Только backend применяет решение, проверяя policy gate, статусы, оффер, источники, права и idempotency до постановки сообщения в outbox.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, PostgreSQL/pgvector, provider adapters, object storage, speech-to-text adapter, controlled search adapter, Pytest, React/TypeScript, Playwright.

## Global Constraints

- Stage 1 Gate принят и manual fallback остаётся работоспособным.
- Реальные Telegram-сообщения загружаются в AI только после `PolicyGate.require_ai_operation`.
- AI никогда не вызывает Telegram connector напрямую.
- Цены, ссылки, промокоды, выплаты и условия берутся только из утверждённой версии оффера.
- Неподтверждённые кандидаты в знания не попадают в retrieval.
- Внешний контент недоверенный и не может менять системные правила.
- При низкой уверенности, конфликте источников, неизвестном языке/формате или ошибке provider выполняется handoff либо ограниченный безопасный retry.

---

### Task 1: Freeze the AI Contract and Provider Boundary

**Files:**
- Create: `apps/api/app/modules/ai/contracts.py`
- Create: `apps/api/app/modules/ai/provider.py`
- Create: `apps/api/app/modules/ai/provider_registry.py`
- Create: `apps/api/app/modules/ai/errors.py`
- Create: `apps/api/tests/modules/ai/test_contract.py`
- Create: `apps/api/tests/modules/ai/test_provider_failover.py`
- Create: `packages/contracts/ai-decision.schema.json`

**Interfaces:**
- Consumes: policy-approved `AiRequest`.
- Produces: schema-valid `AiDecision`; no side effects.

- [ ] Write failing schema tests for required fields, unknown-field rejection, invalid offer IDs, unsupported actions and unsafe status changes.
- [ ] Define the contract:

```python
class AiDecision(BaseModel):
    reply_text: str | None
    language: str
    intent: str
    sentiment: str
    extracted_facts: tuple[ExtractedFact, ...]
    confidence: float = Field(ge=0, le=1)
    sales_stage_transition: SalesStage | None
    contact_permission_action: Literal["set_stop"] | None
    campaign_result: str | None
    offer_id: UUID | None
    offer_reason: str | None
    handoff: bool
    handoff_reason: str | None
    updated_summary: str
    knowledge_candidate: KnowledgeCandidateDraft | None
    source_ids: tuple[str, ...]
```

- [ ] Run `pytest apps/api/tests/modules/ai/test_contract.py -q`; confirm FAIL, then implement strict parsing and size limits.
- [ ] Implement primary/reserve provider adapters with timeouts, one bounded failover and normalized errors.
- [ ] Test timeout, malformed JSON, provider refusal, duplicate callback and both-provider failure; require handoff-safe result.
- [ ] Run `pytest apps/api/tests/modules/ai/test_contract.py apps/api/tests/modules/ai/test_provider_failover.py -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: define provider neutral ai contract"`.

### Task 2: Implement Versioned AI Profiles

**Files:**
- Create: `apps/api/app/modules/ai/profiles.py`
- Create: `apps/api/app/modules/ai/profile_routes.py`
- Create: `apps/api/app/db/migrations/versions/0012_ai_profiles.py`
- Create: `apps/api/tests/modules/ai/test_profiles.py`
- Create: `apps/web/src/features/ai-profiles/profiles-page.tsx`
- Create: `apps/web/src/features/ai-profiles/profile-editor.tsx`

**Interfaces:**
- Consumes: role, domain, goals, tone, terminology, qualification questions, source policy, offers, restrictions and examples.
- Produces: immutable `AiProfileVersion` assigned to campaign or inbound channel.

- [ ] Write failing tests for draft/publish lifecycle, immutable published version, one active profile per dialogue, default-to-new-dialogues and explicit critical propagation.
- [ ] Implement profile version creation and assignment; persist profile version ID on every AI attempt.
- [ ] Implement profile editor sections with a diff preview before publish.
- [ ] Add permission tests: admin/owner can publish; manager can view but not publish.
- [ ] Run backend and frontend tests; expect PASS.
- [ ] Commit with `git commit -m "feat: add versioned ai profiles"`.

### Task 3: Build AI Orchestration with Backend Validation

**Files:**
- Create: `apps/api/app/modules/ai/context_builder.py`
- Create: `apps/api/app/modules/ai/orchestrator.py`
- Create: `apps/api/app/modules/ai/decision_applier.py`
- Create: `apps/api/tests/modules/ai/test_orchestrator.py`
- Create: `apps/api/tests/modules/ai/test_decision_applier.py`

**Interfaces:**
- Consumes: dialogue ID and trigger message ID.
- Produces: audited AI attempt, validated domain actions and optional outbox message.

- [ ] Write a failing test proving policy denial occurs before message text is loaded.
- [ ] Write failing tests for forbidden stop removal, invalid transition, changed offer, unauthorized file/link, manager-owned dialogue and duplicate trigger.
- [ ] Implement orchestration order:

```text
policy gate -> dialogue ownership -> context snapshot -> provider call ->
schema validation -> source/offer/status validation -> audit -> outbox or handoff
```

- [ ] Persist model/provider, profile version, knowledge version set, offer version, token/cost metrics, sources and decision hash.
- [ ] Route all outgoing text through Stage 1 `OutboxRepository`; never call `TelegramGateway` here.
- [ ] Run `pytest apps/api/tests/modules/ai/test_orchestrator.py apps/api/tests/modules/ai/test_decision_applier.py -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: orchestrate validated ai decisions"`.

### Task 4: Implement Client Memory and Summaries

**Files:**
- Create: `apps/api/app/modules/memory/models.py`
- Create: `apps/api/app/modules/memory/service.py`
- Create: `apps/api/app/modules/memory/routes.py`
- Create: `apps/api/app/db/migrations/versions/0013_memory.py`
- Create: `apps/api/tests/modules/memory/test_memory.py`

**Interfaces:**
- Consumes: validated dialogue facts and summary candidate.
- Produces: client-scoped facts, preferences, objections, shown offers, result history and compact context.

- [ ] Write failing tests for organization/client scope, account independence, conflict history, archive persistence and prohibition on automatic promotion to company knowledge.
- [ ] Implement fact records with source message, confidence, valid_from, superseded_by and sensitivity marker.
- [ ] Implement token-budgeted context assembly: current summary, active facts, recent messages and offer history.
- [ ] Test that reconnecting the same identified client exposes prior memory while a new active dialogue is not automatically transferred.
- [ ] Run `pytest apps/api/tests/modules/memory -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: add account independent client memory"`.

### Task 5: Build Versioned Knowledge and RAG

**Files:**
- Create: `apps/api/app/modules/knowledge/models.py`
- Create: `apps/api/app/modules/knowledge/chunker.py`
- Create: `apps/api/app/modules/knowledge/embeddings.py`
- Create: `apps/api/app/modules/knowledge/retrieval.py`
- Create: `apps/api/app/modules/knowledge/routes.py`
- Create: `apps/api/app/db/migrations/versions/0014_knowledge_pgvector.py`
- Create: `apps/api/tests/modules/knowledge/test_versions.py`
- Create: `apps/api/tests/modules/knowledge/test_retrieval.py`

**Interfaces:**
- Consumes: approved source documents/text and retrieval query.
- Produces: published `KnowledgeVersion` and ranked `KnowledgeHit` with source ID.

- [ ] Write failing tests for draft/review/published statuses, immutable published content, tenant isolation and unpublished exclusion.
- [ ] Write retrieval tests for company rules outranking general model knowledge, source preservation and no-hit threshold.
- [ ] Implement deterministic chunk IDs, content hashes, pgvector index and hybrid metadata filters by organization/profile/category/version.
- [ ] Persist the exact knowledge version IDs returned to every AI attempt.
- [ ] Run `pytest apps/api/tests/modules/knowledge -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: add versioned knowledge retrieval"`.

### Task 6: Add Controlled External Search

**Files:**
- Create: `apps/api/app/modules/ai/search.py`
- Create: `apps/api/app/modules/ai/source_policy.py`
- Create: `apps/api/tests/modules/ai/test_search_policy.py`
- Create: `apps/api/tests/modules/ai/test_prompt_injection.py`

**Interfaces:**
- Consumes: query, AI profile search policy and company knowledge.
- Produces: dated `ExternalSourceEvidence` or explicit handoff reason.

- [ ] Write tests for disabled search, changing/current fact trigger, domain allow/deny lists, stale result, source conflict and provider outage.
- [ ] Write prompt-injection fixtures containing instructions to reveal prompts, change price and ignore company policy; require them to remain inert quoted evidence.
- [ ] Implement search results as data-only records:

```python
class ExternalSourceEvidence(BaseModel):
    url: HttpUrl
    title: str
    retrieved_at: datetime
    excerpt: str
    trust_tier: int
    content_hash: str
```

- [ ] Require handoff when external evidence conflicts with approved company data.
- [ ] Run `pytest apps/api/tests/modules/ai/test_search_policy.py apps/api/tests/modules/ai/test_prompt_injection.py -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: add controlled external search"`.

### Task 7: Implement Offers, Tracking and Postback

**Files:**
- Create: `apps/api/app/modules/offers/models.py`
- Create: `apps/api/app/modules/offers/policy.py`
- Create: `apps/api/app/modules/offers/tracking.py`
- Create: `apps/api/app/modules/offers/routes.py`
- Create: `apps/api/app/db/migrations/versions/0015_offers.py`
- Create: `apps/api/tests/modules/offers/test_offer_policy.py`
- Create: `apps/api/tests/modules/offers/test_tracking.py`

**Interfaces:**
- Consumes: campaign/contact/profile and proposed offer ID.
- Produces: `ValidatedOffer`, redirect event and idempotent postback result.

- [ ] Write failing tests for active period, audience/geography, priority, campaign/profile association, limits, required/prohibited wording and immutable version.
- [ ] Prove AI cannot change price, link, promo code or conditions by testing mutated provider output against `OfferPolicy`.
- [ ] Implement signed internal redirect carrying opaque campaign/contact/offer/variant IDs.
- [ ] Implement idempotent postback statuses `click, lead, pending, approved, rejected`, payout, currency and source audit.
- [ ] Run `pytest apps/api/tests/modules/offers -q`; expect PASS.
- [ ] Commit with `git commit -m "feat: add governed offers and partner tracking"`.

### Task 8: Add Language, Voice, Attachments and AI UI

**Files:**
- Create: `apps/api/app/modules/ai/speech.py`
- Create: `apps/api/app/modules/ai/media_policy.py`
- Create: `apps/api/tests/modules/ai/test_speech.py`
- Create: `apps/api/tests/modules/ai/test_media_policy.py`
- Create: `apps/web/src/features/ai-profiles/profile-editor.tsx`
- Create: `apps/web/src/features/knowledge/knowledge-page.tsx`
- Create: `apps/web/src/features/offers/offers-page.tsx`
- Modify: `apps/web/src/features/inbox/dialog-page.tsx`

**Interfaces:**
- Consumes: voice/media message and approved materials.
- Produces: transcript with confidence, manager handoff or approved outbound attachment.

- [ ] Write tests for language detection, missing-language materials, low-quality transcription, speech provider timeout, image/video/document handoff and approved attachment send.
- [ ] Implement STT adapter with original object reference, transcript, confidence and provider metadata; do not place raw media in model context automatically.
- [ ] Implement UI for profiles, knowledge versions, sources, offers and AI attempt evidence.
- [ ] Show AI drafts after manager handoff but prevent automatic send until explicit return-to-AI action.
- [ ] Run backend and frontend tests; expect PASS.
- [ ] Commit with `git commit -m "feat: add multilingual voice and ai management ui"`.

### Task 9: Build AI Evaluation and Stage 2 End-to-End Gate

**Files:**
- Create: `tests/fixtures/ai/evaluation_cases.jsonl`
- Create: `apps/api/tests/evaluation/test_ai_scenarios.py`
- Create: `apps/api/tests/evaluation/test_policy_gate.py`
- Create: `tests/e2e/stage2-ai-dialog.spec.ts`
- Create: `docs/runbooks/ai-evaluation.md`
- Create: `docs/architecture/stage-2-gate-report.md`

**Interfaces:**
- Consumes: all Stage 2 services and fixed evaluation data.
- Produces: reproducible safety/quality report and Gate 2 decision.

- [ ] Encode cases for knowledge answer, current search, source conflict, unauthorized discount, human request, ordinary refusal, explicit no-contact, low confidence, missing language, poor voice, prompt injection and tracked partner offer.
- [ ] Assert hard invariants independently of wording: no changed offer facts, no unauthorized action, correct stop distinction, source IDs present, correct handoff and no send after manager ownership.
- [ ] Add policy matrix cases for missing, expired, revoked, wrong-organization, wrong-channel and approved records; denial must occur before content loading.
- [ ] Run `pytest apps/api/tests/evaluation -q`; require zero hard-invariant failures.
- [ ] Run `pnpm --dir apps/web exec playwright test tests/e2e/stage2-ai-dialog.spec.ts`; cover inbound message through AI response/handoff and manager takeover.
- [ ] Record provider/model versions, dataset hash, scores, failures and approval in the Stage 2 gate report.
- [ ] Commit with `git commit -m "test: prove controlled ai dialogue"`.

## Stage 2 Exit Checklist

- [ ] AI contract rejects malformed or unauthorized decisions.
- [ ] Policy denial occurs before real Telegram message content is loaded.
- [ ] Every AI attempt records profile, knowledge, offer, provider and source versions.
- [ ] Backend alone applies status, offer, stop and send actions.
- [ ] Memory remains client- and organization-scoped across account changes.
- [ ] Only published knowledge participates in retrieval.
- [ ] External search preserves evidence and treats page instructions as untrusted.
- [ ] Offer facts and tracking cannot be altered by model output.
- [ ] Voice/language/media failure paths hand off safely.
- [ ] Manager ownership disables automatic AI sending.
- [ ] Fixed AI evaluation has zero hard-invariant failures.
- [ ] Stage 2 Gate report is approved before Stage 3 starts.
