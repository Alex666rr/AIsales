# Trello Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the AIsales Trello board into a legible project roadmap built from the committed Stage 0–3 plans.

**Architecture:** A local Python synchronizer owns a declarative roadmap manifest. It maps each plan task to a readable card in its stage list and uses explicit visible card identifiers rather than hidden markup. A narrow migration archives only known generated and empty Trello starter lists.

**Tech Stack:** Python 3.13 standard library, pytest, Trello REST API.

## Global Constraints

- Trello credentials stay in local `.env` and never enter GitHub, Railway, logs, or card content.
- The command runs only on the owner’s computer after meaningful completed project blocks.
- Do not delete board data; archive only explicit obsolete generated/starter lists.
- Preserve manual comments, checklists, labels, attachments, and unknown lists.

---

### Task 1: Readable roadmap manifest and card synchronization

**Files:**
- Modify: `tools/trello/sync_board.py`
- Modify: `tools/trello/test_sync_board.py`

**Interfaces:**
- Produces: `RoadmapCard` records and `TrelloBoardSynchronizer.sync()` that creates the six target lists and roadmap cards.
- Consumes: the committed Stage 0–3 plan paths.

- [ ] Write RED tests for `S1.03` card creation, visible Russian status, and absence of HTML markers.
- [ ] Run `python -m pytest tools/trello/test_sync_board.py -q` and confirm expected failure.
- [ ] Implement the manifest, six-list structure, human-readable descriptions, and status labels.
- [ ] Run the focused suite and confirm GREEN.

### Task 2: Safe migration and operator runbook

**Files:**
- Modify: `tools/trello/sync_board.py`
- Modify: `tools/trello/test_sync_board.py`
- Modify: `docs/runbooks/trello-project-board.md`

**Interfaces:**
- Produces: archive-only migration of known obsolete lists and a guide that explains how to read/update the roadmap.

- [ ] Write RED tests proving unrecognized lists survive while only old generated/starter lists are archived.
- [ ] Implement list archival through Trello’s `closed=true` operation.
- [ ] Update the operator runbook with the six-list model and regular local synchronization cadence.
- [ ] Run focused tests, then `python -m pytest -q`.
- [ ] Run the live migration twice; the second run must report zero creates, updates, and archives.
- [ ] Commit and push the completed roadmap change.
