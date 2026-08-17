# Trello Project Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an idempotent, local-only command that keeps a small AIsales roadmap board structure in Trello without overwriting human work.

**Architecture:** A dependency-free Python command owns a tiny Trello REST client behind an injectable transport. A committed manifest describes only the delivery milestones. Card descriptions contain a namespaced marker that lets the command update its own managed status while preserving human edits.

**Tech Stack:** Python 3.13 standard library, pytest, Trello REST API.

## Global Constraints

- Read credentials only from local `TRELLO_API_KEY`, `TRELLO_TOKEN`, and `TRELLO_BOARD_ID` variables.
- Never write credentials to Git, logs, card text, or test snapshots.
- The command is local-only; Railway must not run it.
- A rerun must not duplicate lists or managed cards and must not move a card that a human moved.

---

### Task 1: Trello board client and idempotent synchronizer

**Files:**
- Create: `tools/trello/sync_board.py`
- Create: `tools/trello/test_sync_board.py`

**Interfaces:**
- Produces: `TrelloBoardSynchronizer.from_environment()` and `sync()`.
- Consumes: standard-library `urllib.request` transport and the three Trello variables.

- [ ] **Step 1: Write failing tests**

```python
def test_sync_creates_missing_lists_and_cards_once():
    result = synchronizer.sync()
    assert result.created_lists == 4
    assert result.created_cards > 0
    assert synchronizer.sync().created_cards == 0

def test_sync_preserves_human_card_text_and_list_position():
    synchronizer.sync()
    fake.move_card_to("stage-1", "Готово")
    fake.append_human_note("stage-1", "Проверить вручную")
    synchronizer.sync()
    assert fake.card("stage-1").list_name == "Готово"
    assert "Проверить вручную" in fake.card("stage-1").description
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python -m pytest tools/trello/test_sync_board.py -q`

- [ ] **Step 3: Implement minimal client, manifest, markers, and sync logic**

```python
class TrelloBoardSynchronizer:
    @classmethod
    def from_environment(cls) -> "TrelloBoardSynchronizer": ...

    def sync(self) -> SyncResult:
        """Create missing board lists and upsert only managed metadata."""
```

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python -m pytest tools/trello/test_sync_board.py -q`

- [ ] **Step 5: Commit**

```bash
git add tools/trello
git commit -m "feat: add idempotent trello project sync"
```

### Task 2: Operator configuration and live board initialization

**Files:**
- Create: `.env.example`
- Create: `docs/runbooks/trello-project-board.md`
- Modify: `.gitignore`
- Test: `tools/trello/test_sync_board.py`

**Interfaces:**
- Consumes: `python tools/trello/sync_board.py`.
- Produces: a reproducible local setup command and an initialized user board.

- [ ] **Step 1: Write failing tests for missing credentials and redacted output**

```python
def test_missing_variable_is_actionable_and_does_not_echo_values():
    with pytest.raises(ConfigurationError, match="TRELLO_TOKEN"):
        TrelloBoardSynchronizer.from_environment({"TRELLO_API_KEY": "secret"})
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python -m pytest tools/trello/test_sync_board.py -q`

- [ ] **Step 3: Add example variables and a runbook**

```env
TRELLO_API_KEY=
TRELLO_TOKEN=
TRELLO_BOARD_ID=
```

Document the local command, idempotency guarantee, token revocation path, and explicit prohibition on adding `.env` to Git.

- [ ] **Step 4: Run focused and full test suites**

Run: `python -m pytest tools/trello/test_sync_board.py -q`
Run: `python -m pytest -q`

- [ ] **Step 5: Initialize the authenticated board once**

Run: `python tools/trello/sync_board.py`

Expected: the four lists and milestone cards are created or updated with no secret output.

- [ ] **Step 6: Commit**

```bash
git add .env.example .gitignore docs/runbooks/trello-project-board.md tools/trello
git commit -m "docs: add trello board operations"
```
