# AI Sales Manager Stage 0 Telegram Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Подтвердить техническую реализуемость подключения заявленных Telegram-аккаунтов, ботов, сессий и прокси без подключения AI к реальным Telegram-данным.

**Architecture:** Stage 0 создаёт изолированный Python package `telegram_connector` и минимальный FastAPI control API. Все способы авторизации реализуют единый adapter contract; результаты проб сохраняются в локальной PostgreSQL-схеме, а любые AI-операции закрыты server-side policy gate.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, Telethon, cryptography, PostgreSQL, Pytest, Docker Compose.

## Global Constraints

- Выполнять только на тестовых Telegram-аккаунтах, которыми владеет проект.
- Не использовать AI для содержимого реальных Telegram-диалогов.
- Не хранить исходные TData/session uploads после преобразования.
- Не выводить телефоны, коды, пароли 2FA, токены, session strings и proxy credentials в логи.
- Не считать успешный login подтверждением допустимости продуктового сценария.
- Для каждого session-класса создать воспроизводимую fixture-инструкцию и запись совместимости.
- Каждый сетевой эффект скрыть за adapter interface и покрыть contract tests.

---

### Task 1: Bootstrap Prototype Workspace

**Files:**
- Create: `pyproject.toml`
- Create: `services/telegram_connector/__init__.py`
- Create: `services/telegram_connector/config.py`
- Create: `services/telegram_connector/models.py`
- Create: `services/telegram_connector/tests/test_config.py`
- Create: `apps/api/app/main.py`
- Create: `apps/api/app/config.py`
- Create: `apps/api/app/db/base.py`
- Create: `apps/api/app/db/session.py`
- Create: `apps/api/app/db/migrations/env.py`
- Create: `infra/docker-compose.prototype.yml`
- Create: `infra/Dockerfile`

**Interfaces:**
- Consumes: environment variables `DATABASE_URL`, `SESSION_ENCRYPTION_KEY`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`.
- Produces: `ConnectorSettings` and shared immutable prototype value objects.

- [ ] **Step 1: Write the failing settings test**

```python
def test_settings_reject_missing_encryption_key(monkeypatch):
    monkeypatch.delenv("SESSION_ENCRYPTION_KEY", raising=False)
    with pytest.raises(ValidationError):
        ConnectorSettings()
```

- [ ] **Step 2: Run the test and confirm the expected failure**

Run: `pytest services/telegram_connector/tests/test_config.py -q`  
Expected: FAIL because `ConnectorSettings` does not exist.

- [ ] **Step 3: Implement validated settings and immutable value objects**

```python
class ConnectorSettings(BaseSettings):
    database_url: PostgresDsn
    session_encryption_key: SecretStr
    telegram_api_id: int
    telegram_api_hash: SecretStr
    environment: Literal["test", "prototype"] = "test"
```

Define `AccountId`, `OrganizationId`, `ProxyId` as UUID aliases and `UtcTimestamp` serialization helpers in `models.py`. Add a minimal FastAPI composition root and async SQLAlchemy session used by the policy gate.

- [ ] **Step 4: Add prototype containers**

Use `python:3.13-slim`; run PostgreSQL with a health check; mount no session upload directory persistently.

- [ ] **Step 5: Run verification**

Run: `pytest services/telegram_connector/tests/test_config.py -q && docker compose -f infra/docker-compose.prototype.yml config -q`  
Expected: PASS and valid Compose configuration.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml services/telegram_connector infra
git commit -m "build: bootstrap telegram prototype"
```

### Task 2: Define Session Adapter and Quarantine Contracts

**Files:**
- Create: `services/telegram_connector/adapters/base.py`
- Create: `services/telegram_connector/session_store.py`
- Create: `services/telegram_connector/quarantine.py`
- Create: `services/telegram_connector/tests/test_session_contract.py`
- Create: `services/telegram_connector/tests/test_quarantine.py`

**Interfaces:**
- Consumes: `ConnectorSettings`, uploaded bytes and adapter-specific credentials.
- Produces: `SessionAdapter.probe(material) -> SessionProbeResult`, `EncryptedSessionStore.put(account_id, payload) -> SessionRef`.

- [ ] **Step 1: Write contract tests for every adapter**

```python
@pytest.mark.parametrize("adapter_name", ["phone", "qr", "tdata", "telethon_file", "telethon_string", "bot"])
def test_adapter_returns_normalized_probe_result(adapter_name, adapter_registry, valid_material):
    result = adapter_registry[adapter_name].probe(valid_material[adapter_name])
    assert result.adapter == adapter_name
    assert result.state in {"authorized", "needs_code", "needs_2fa", "invalid", "unsupported"}
```

- [ ] **Step 2: Write quarantine deletion tests**

Assert that successful conversion and conversion failure both remove the original upload path and that logs contain only a generated upload ID.

- [ ] **Step 3: Run tests and confirm failures**

Run: `pytest services/telegram_connector/tests/test_session_contract.py services/telegram_connector/tests/test_quarantine.py -q`  
Expected: FAIL because adapter and quarantine contracts do not exist.

- [ ] **Step 4: Implement contracts**

```python
class SessionAdapter(Protocol):
    name: str
    async def probe(self, material: SessionMaterial) -> SessionProbeResult: ...
    async def convert(self, material: SessionMaterial) -> bytes: ...

class SessionProbeResult(BaseModel):
    adapter: str
    state: Literal["authorized", "needs_code", "needs_2fa", "invalid", "unsupported"]
    telegram_user_id: int | None
    username: str | None
    phone_masked: str | None
    capabilities: frozenset[str]
    error_code: str | None
```

- [ ] **Step 5: Implement encrypted storage and guaranteed cleanup**

Encrypt session bytes with an authenticated cipher, store only ciphertext and key version, and perform upload deletion in a `finally` block.

- [ ] **Step 6: Run tests**

Run: `pytest services/telegram_connector/tests/test_session_contract.py services/telegram_connector/tests/test_quarantine.py -q`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/telegram_connector
git commit -m "feat: add session quarantine contracts"
```

### Task 3: Implement Authorization Adapters

**Files:**
- Create: `services/telegram_connector/adapters/phone.py`
- Create: `services/telegram_connector/adapters/qr.py`
- Create: `services/telegram_connector/adapters/tdata.py`
- Create: `services/telegram_connector/adapters/telethon_session.py`
- Create: `services/telegram_connector/adapters/bot.py`
- Create: `services/telegram_connector/adapters/registry.py`
- Create: `services/telegram_connector/tests/test_authorization_adapters.py`
- Create: `tests/fixtures/telegram/README.md`

**Interfaces:**
- Consumes: `SessionAdapter`, Telethon client factory and owned test credentials.
- Produces: `AdapterRegistry.get(format_name) -> SessionAdapter` for six required classes.

- [ ] **Step 1: Write adapter tests with a fake Telethon client**

Cover phone code request, invalid code, required 2FA, QR expiry/retry, TData conversion success/failure, Telethon file/string sessions, invalid bot token and authorized bot.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest services/telegram_connector/tests/test_authorization_adapters.py -q`  
Expected: FAIL because concrete adapters are absent.

- [ ] **Step 3: Implement phone and QR state machines**

```python
class AuthStep(BaseModel):
    state: Literal["code_sent", "needs_2fa", "authorized", "expired", "failed"]
    challenge_id: UUID
    expires_at: datetime
    safe_message: str
```

Store challenge state server-side; never return session material or Telegram API secrets.

- [ ] **Step 4: Implement import adapters**

Accept only explicitly registered archive/file signatures; reject path traversal, oversized archives, unknown formats and incompatible schema versions before conversion.

- [ ] **Step 5: Implement bot adapter and registry**

Validate token through the Bot API adapter, normalize bot identity and keep bot sessions separate from user-account MTProto sessions.

- [ ] **Step 6: Document fixture creation**

In `tests/fixtures/telegram/README.md`, document how an authorized developer creates local ignored fixtures and the exact filenames expected by manual tests. Confirm `.gitignore` excludes every secret fixture path.

- [ ] **Step 7: Run tests**

Run: `pytest services/telegram_connector/tests/test_authorization_adapters.py -q`  
Expected: PASS with no live network requirement.

- [ ] **Step 8: Commit**

```bash
git add services/telegram_connector tests/fixtures/telegram/README.md .gitignore
git commit -m "feat: implement telegram authorization adapters"
```

### Task 4: Validate Proxy and Connection Lifecycle

**Files:**
- Create: `services/telegram_connector/proxies.py`
- Create: `services/telegram_connector/runtime/connection.py`
- Create: `services/telegram_connector/runtime/supervisor.py`
- Create: `services/telegram_connector/tests/test_proxy_matrix.py`
- Create: `services/telegram_connector/tests/test_connection_lifecycle.py`

**Interfaces:**
- Consumes: encrypted `SessionRef`, `ProxyConfig` and Telethon client factory.
- Produces: `ConnectionSupervisor.start(account_id)`, `stop(account_id)`, `health(account_id) -> ConnectionHealth`.

- [ ] **Step 1: Write proxy matrix tests**

Cover SOCKS5 and HTTP(S), authentication, shared capacity 1–5, account override precedence, unavailable proxy and credential redaction.

- [ ] **Step 2: Write lifecycle tests**

Cover startup, reconnect after process restart, explicit pause, authorization loss, rate-limit state, graceful stop and archive.

- [ ] **Step 3: Run tests and confirm failures**

Run: `pytest services/telegram_connector/tests/test_proxy_matrix.py services/telegram_connector/tests/test_connection_lifecycle.py -q`  
Expected: FAIL because supervisor and proxy services are absent.

- [ ] **Step 4: Implement proxy and health models**

```python
class ConnectionHealth(BaseModel):
    state: Literal["quarantine", "active", "paused", "reauth_required", "limited", "blocked", "archived"]
    last_seen_at: datetime | None
    proxy_ip: str | None
    latency_ms: int | None
    error_code: str | None
```

- [ ] **Step 5: Implement supervised lifecycle**

Use bounded exponential reconnect, persist state before retry, never rotate proxy automatically to bypass Telegram restrictions, and stop sending when proxy health is unavailable.

- [ ] **Step 6: Run tests**

Run: `pytest services/telegram_connector/tests/test_proxy_matrix.py services/telegram_connector/tests/test_connection_lifecycle.py -q`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/telegram_connector
git commit -m "feat: supervise telegram connections and proxies"
```

### Task 5: Prove Message Roundtrip and Compatibility Registry

**Files:**
- Create: `services/telegram_connector/gateway.py`
- Create: `services/telegram_connector/error_codes.py`
- Create: `services/telegram_connector/compatibility.py`
- Create: `services/telegram_connector/tests/test_gateway.py`
- Create: `services/telegram_connector/tests/test_error_taxonomy.py`
- Create: `services/telegram_connector/tests/manual/test_live_roundtrip.py`
- Create: `docs/architecture/telegram-compatibility.md`

**Interfaces:**
- Consumes: active connection, normalized peer ID and idempotency key.
- Produces: `TelegramGateway.send`, normalized incoming `TelegramUpdate`, `CompatibilityRegistry`.

- [ ] **Step 1: Write gateway contract tests**

```python
async def test_send_returns_same_result_for_same_idempotency_key(gateway):
    first = await gateway.send(command(idempotency_key="fixed-key"))
    second = await gateway.send(command(idempotency_key="fixed-key"))
    assert first.external_message_id == second.external_message_id
    assert gateway.client.send_count == 1
```

Cover incoming private message normalization and exclusion of groups, channels and service chats.

- [ ] **Step 2: Write error taxonomy tests**

Map invalid peer, privacy restriction, paid-message requirement, flood wait, auth loss, blocked account, timeout and unknown Telegram errors to stable internal codes.

- [ ] **Step 3: Run tests and confirm failures**

Run: `pytest services/telegram_connector/tests/test_gateway.py services/telegram_connector/tests/test_error_taxonomy.py -q`  
Expected: FAIL because gateway and taxonomy are absent.

- [ ] **Step 4: Implement gateway and registry**

Persist the idempotency record before sending, reconcile uncertain results before retry, and store adapter/version/proxy/message outcomes in `CompatibilityRegistry`.

- [ ] **Step 5: Add opt-in live test**

Require `RUN_TELEGRAM_LIVE_TESTS=1`; send one message between two owned test accounts, receive it, restart the connector, receive a reply and archive the sender session.

- [ ] **Step 6: Run automated and live verification**

Run: `pytest services/telegram_connector/tests -q`  
Expected: PASS; live test SKIPPED unless explicitly enabled.

Run in an approved test environment: `RUN_TELEGRAM_LIVE_TESTS=1 pytest services/telegram_connector/tests/manual/test_live_roundtrip.py -q -s`  
Expected: PASS and one compatibility row per adapter/proxy combination.

- [ ] **Step 7: Commit**

```bash
git add services/telegram_connector docs/architecture/telegram-compatibility.md
git commit -m "feat: prove telegram message roundtrip"
```

### Task 6: Implement the Telegram/AI Approval Gate

**Files:**
- Create: `apps/api/app/modules/policy/models.py`
- Create: `apps/api/app/modules/policy/repository.py`
- Create: `apps/api/app/modules/policy/service.py`
- Create: `apps/api/app/modules/policy/routes.py`
- Create: `apps/api/app/db/migrations/versions/0001_policy_gate.py`
- Create: `apps/api/tests/modules/policy/test_ai_gate.py`
- Create: `docs/architecture/telegram-ai-approval-record.md`
- Create: `docs/architecture/stage-0-gate-report.md`

**Interfaces:**
- Consumes: organization, channel type, data category, operation, current terms revision and approval record.
- Produces: `PolicyGate.require_ai_operation(context) -> ApprovalDecision`.

- [ ] **Step 1: Write default-deny tests**

```python
def test_real_telegram_message_is_denied_without_matching_approval(policy_gate):
    decision = policy_gate.evaluate(real_message_context(channel="mtproto_user"))
    assert decision.allowed is False
    assert decision.reason_code == "approval_missing"
```

Also cover expired approval, wrong channel, wrong organization, unlisted operation, revoked approval and allowed synthetic non-Telegram evaluation.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest apps/api/tests/modules/policy/test_ai_gate.py -q`  
Expected: FAIL because the policy module is absent.

- [ ] **Step 3: Implement approval records**

```python
class AiApprovalRecord(BaseModel):
    organization_id: UUID
    channel_types: frozenset[Literal["mtproto_user", "bot_api"]]
    data_categories: frozenset[str]
    operations: frozenset[Literal["draft", "auto_reply", "summarize", "classify"]]
    terms_revision: str
    evidence_uri: str
    approved_by: UUID
    approved_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
```

- [ ] **Step 4: Implement server-side enforcement**

The policy service returns a decision object; calling application services must abort before loading message text when denied. Approval writes require platform-owner authorization and immutable audit.

- [ ] **Step 5: Persist approval records**

Create the `ai_approval_records` migration and repository query that matches organization, channel, data category, operation, validity window and revocation state. Default to denial when the table is unavailable or no exact record matches.

- [ ] **Step 6: Write decision and gate templates**

The approval record template must contain the date, checked terms revisions, channel, data, operations, restrictions, responsible person, evidence and explicit approve/deny outcome. The Stage 0 report records technical findings separately from the policy decision.

- [ ] **Step 7: Run Stage 0 verification**

Run: `pytest services/telegram_connector/tests apps/api/tests/modules/policy -q`  
Expected: PASS; no real Telegram-to-AI path exists.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/modules/policy apps/api/app/db/migrations apps/api/tests/modules/policy docs/architecture
git commit -m "feat: enforce telegram ai approval gate"
```

## Stage 0 Exit Checklist

- [ ] Phone/code/2FA and QR flows have reproducible results.
- [ ] Target TData and Telethon file/string session classes have registry entries.
- [ ] Bot token flow is isolated from user-account sessions.
- [ ] SOCKS5, HTTP(S), authenticated and shared proxy scenarios have recorded results.
- [ ] Connector recovers after restart and classifies Telegram failures.
- [ ] Upload cleanup, encryption and log-redaction tests pass.
- [ ] Private incoming and outgoing test messages complete roundtrip.
- [ ] Real Telegram content cannot reach AI without a matching approval.
- [ ] `docs/architecture/stage-0-gate-report.md` contains an explicit accepted or rejected result.
