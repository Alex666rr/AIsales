# Trello Roadmap Design

## Purpose

The AIsales Trello board is a personal delivery map for the project owner. It answers four questions without needing a chat: what is complete, what is being worked on now, what comes next, and where the authoritative plan lives.

## Board structure

The board has six lists:

1. `📍 Сейчас` — a reading guide and the next actionable project block.
2. `✅ Stage 0 — Telegram prototype` — completed Stage 0 tasks.
3. `🚧 Stage 1 — Sales infrastructure` — Stage 1 tasks, including completed access work and the next web-administration task.
4. `🗂 Stage 2 — AI manager` — future tasks.
5. `🗂 Stage 3 — Quality and optimization` — future tasks.
6. `📦 Архив` — completed, superseded, or manually retired board content.

Each card title starts with a stable human-readable identifier such as `S1.03`. Its description has the objective, status, next action where relevant, and a relative path to its authoritative plan. There are no hidden HTML comments or credentials in Trello.

## Synchronization

The local command remains the sole writer. It uses a code manifest derived from the committed Stage 0–3 plans. The manifest holds card IDs, Russian titles, status labels, and plan paths. Synchronization creates missing labels/lists/cards and updates only the card description sections it owns. It never uploads project files, Telegram data, or secrets.

The command runs locally after meaningful completed development blocks. This keeps Trello current while the Trello token stays on the owner’s PC. Automatic GitHub or Railway synchronization is intentionally excluded because it would require storing the Trello token in an external service.

## Safe migration

The previous generated lists and cards are archived rather than deleted. Empty Trello starter lists are also archived. The migration only archives the explicit old generated names and the three empty starter lists; unrecognized human lists and cards are left untouched.

## Verification

Tests prove creation of the roadmap, card identity without visible markers, preservation of human card comments, idempotent re-runs, and selective archival of only the known old lists. A live board migration is performed once after the automated tests pass and then immediately repeated to prove no duplicate changes.
