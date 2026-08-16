# Безопасный импорт Telegram Desktop `tdata` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать проверяемый локальный импортёр одной версии `tdata`, который создаёт только зашифрованную контролируемую Telegram-сессию или безопасно завершается без следов.

**Architecture:** Импорт разделён на offline preflight/parser и отдельный opt-in verification adapter. Raw `tdata` никогда не входит в HTTP API или Railway; parser не имеет сетевой зависимости, а полученный SessionMaterial передаётся в существующие границы quarantine/encrypted SessionRepository только после явного подтверждения владельца.

**Tech Stack:** Python 3.13, существующий `telegram_connector`, `cryptography`, Telethon, PostgreSQL session repository, pytest.

## Global Constraints

- Не добавлять `opentele`, `opentele2`, session converters или fingerprint spoofing как runtime dependency.
- Тесты используют только синтетические недействительные fixtures; реальный `tdata`, session string, номер, API hash и passcode не попадают в репозиторий.
- Первый запуск только local CLI с закрытым Telegram Desktop; raw `tdata` не загружается в Railway.
- Любая ошибка — fail closed, cleanup в `finally`, отсутствие секретов в exception/message/log.
- Любое чтение пути выполняет no-follow/reparse-point checks и проверку неизменности снимка.

---

### Task 1: Зафиксировать контракт preflight и синтетические fixtures

**Files:**
- Create: `services/telegram_connector/importers/tdata/models.py`
- Create: `services/telegram_connector/importers/tdata/preflight.py`
- Create: `services/telegram_connector/tests/importers/test_tdata_preflight.py`
- Create: `services/telegram_connector/tests/fixtures/tdata-portable-7.0.9-invalid/README.md`

**Interfaces:**
- Produces: `TdataSnapshot(root: Path, digest: bytes, total_bytes: int, format_variant: str)`.
- Produces: `prepare_tdata_copy(source: Path, destination: Path, *, max_bytes: int) -> TdataSnapshot`.

- [ ] **Step 1: Write failing tests for a valid synthetic Portable 7.0.9 layout, missing key metadata, symlink/reparse point, oversized tree and source mutation after hashing.**

```python
def test_preflight_rejects_a_source_that_changes_after_hashing(tmp_path: Path) -> None:
    source = make_synthetic_tdata(tmp_path / "source")
    snapshot = prepare_tdata_copy(source, tmp_path / "copy", max_bytes=1_000_000)
    mutate_fixture(source)
    with pytest.raises(TdataSourceChanged):
        snapshot.assert_unchanged()
```

- [ ] **Step 2: Run `pytest services/telegram_connector/tests/importers/test_tdata_preflight.py -q` and confirm RED because the importer does not exist.**

- [ ] **Step 3: Implement only no-follow tree walk, copy-to-private-tempdir, SHA-256 manifest and a narrow Portable 7.0.9 layout detector. Do not decrypt or contact Telegram.**

- [ ] **Step 4: Re-run the focused tests and the full connector suite; confirm GREEN.**

- [ ] **Step 5: Commit `feat: add guarded tdata preflight`.**

### Task 2: Implement offline parser behind a non-secret material boundary

**Files:**
- Create: `services/telegram_connector/importers/tdata/parser.py`
- Create: `services/telegram_connector/importers/tdata/errors.py`
- Create: `services/telegram_connector/tests/importers/test_tdata_parser.py`
- Modify: `services/telegram_connector/importers/tdata/models.py`

**Interfaces:**
- Consumes: `TdataSnapshot` and an injected `DesktopPasscodeReader`.
- Produces: opaque `ImportedAuthorizationMaterial`, marked `repr=False`, non-serializable and single-use.
- Produces: `parse_tdata(snapshot: TdataSnapshot, passcode: SecretStr | None) -> ImportedAuthorizationMaterial`.

- [ ] **Step 1: Write failing fixtures/tests for wrong magic bytes, unsupported format version, empty passcode, incorrect passcode and a fixture with no account. Assert public exceptions contain no source path, key bytes or passcode.**

```python
def test_wrong_passcode_is_redacted(tmp_path: Path) -> None:
    snapshot = synthetic_encrypted_snapshot(tmp_path)
    with pytest.raises(TdataPasscodeRejected) as error:
        parse_tdata(snapshot, SecretStr("wrong"))
    assert "wrong" not in str(error.value)
```

- [ ] **Step 2: Run the focused parser test and confirm RED.**

- [ ] **Step 3: Implement a clean-room, bounded parser from documented/official Telegram Desktop format behaviour. Keep network libraries out of this module and reject unsupported versions rather than guessing.**

- [ ] **Step 4: Re-run focused and full connector tests; confirm GREEN.**

- [ ] **Step 5: Commit `feat: parse supported tdata offline`.**

### Task 3: Build controlled verification and one-time encrypted persistence

**Files:**
- Create: `services/telegram_connector/importers/tdata/verification.py`
- Create: `services/telegram_connector/cli/import_tdata.py`
- Create: `services/telegram_connector/tests/importers/test_tdata_verification.py`
- Modify: `services/telegram_connector/session.py`
- Modify: `services/telegram_connector/persistence.py`

**Interfaces:**
- Consumes: `ImportedAuthorizationMaterial`, injected `TelegramAuthorizationVerifier`, explicit `OwnerCapability`.
- Produces: `VerifiedImportedSession(account_id: int, masked_phone: str | None, encrypted_session: EncryptedSession)`.
- Produces: `verify_and_persist_import(...) -> VerifiedImportedSession`.

- [ ] **Step 1: Write failing tests proving verification never runs before preflight/parser success, a mismatched account is rejected, raw material cannot be serialized, cancellation deletes temporary data, and database persistence receives ciphertext only.**

```python
async def test_cancelled_import_removes_the_private_snapshot(...) -> None:
    with pytest.raises(asyncio.CancelledError):
        await verify_and_persist_import(...)
    assert not private_snapshot_path.exists()
    assert repository.saved_sessions == []
```

- [ ] **Step 2: Run the focused verification tests and confirm RED.**

- [ ] **Step 3: Implement explicit operator consent, controlled Telegram verification through project-owned API credentials, existing encryption/quarantine repositories, and `try/finally` disposal. The CLI prints only masked identity and an audit reference.**

- [ ] **Step 4: Run focused tests, full suite, wheel smoke and `git diff --check`; confirm GREEN.**

- [ ] **Step 5: Commit `feat: verify encrypted tdata imports`.**

### Task 4: Execute a manual test-account B acceptance run and security review

**Files:**
- Create: `docs/deployment/tdata-local-import.md`
- Create: `.superpowers/sdd/<date>-tdata-import/task-4-report.md`
- Modify: `docs/architecture/telegram-compatibility.md`

**Interfaces:**
- Consumes: the local CLI from Task 3 and a user-owned closed Telegram Desktop Portable profile.
- Produces: an audited acceptance result with no raw credentials, paths or account identifiers.

- [ ] **Step 1: Document exact prerequisites: test-only account, Telegram Desktop fully closed, local copy only, no screenshots/archives in chat, recovery path through Telegram Devices.**

- [ ] **Step 2: Run the CLI on the owner-operated test account B; validate only a masked identity, restart, and explicit revocation from Telegram Devices.**

- [ ] **Step 3: Confirm the source `tdata` was untouched, temp snapshot was deleted, and database has only encrypted session data.**

- [ ] **Step 4: Request independent code review focused on parser correctness, license provenance, secret handling and egress behaviour.**

- [ ] **Step 5: Commit `docs: record tdata import acceptance gate`.**

### Task 5: Decide on server-side import separately

**Files:**
- Create: `docs/architecture/tdata-server-import-decision.md`
- Test: deployment policy/repository configuration tests if approved.

**Interfaces:**
- Consumes: successful Task 4 acceptance report and security review.
- Produces: an explicit GO/NO-GO decision; no implicit Railway deployment.

- [ ] **Step 1: Write a decision record requiring a dedicated worker, no public HTTP raw-file upload, isolated storage, egress allow-list to Telegram and PostgreSQL only, malware scan and retention policy before server-side processing.**

- [ ] **Step 2: Confirm Railway’s current shared API service does not meet those controls; record NO-GO if a dedicated environment is not available.**

- [ ] **Step 3: Commit `docs: gate server-side tdata imports`.**
