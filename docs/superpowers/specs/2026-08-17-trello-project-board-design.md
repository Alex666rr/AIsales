# Trello Project Board Design

## Purpose

Use Trello as a shared, human-editable view of the AIsales delivery roadmap. It is not a system of record for application data, credentials, deployments, or source code.

## Scope

The integration creates and maintains one predictable board structure:

- `Бэклог`
- `В работе`
- `На проверке`
- `Готово`

It creates a small set of idempotent milestone cards from a committed project manifest. A card is identified by an internal marker in its description, so repeated runs update the same card instead of creating duplicates. Existing human text outside that marker and any human-managed labels, checklists, comments, attachments, members, and list moves are never overwritten.

## Data flow

`tools/trello/sync_board.py` reads only three local environment variables: `TRELLO_API_KEY`, `TRELLO_TOKEN`, and `TRELLO_BOARD_ID`. It sends HTTPS requests to Trello's REST API, discovers or creates the four lists, then upserts manifest cards. It prints names, counts, and links only; it never prints keys or tokens.

The manifest contains the project stages and their current high-level status. It deliberately does not read, upload, or mirror source code, Telegram sessions, Railway credentials, or application database data.

## Error handling and safety

Missing variables, an unauthorized token, a closed board, malformed API responses, and network failures cause a non-zero exit without any board mutation after the failed request. The script uses no third-party dependency and does not run from the API or Railway runtime. It is an explicit local operator command.

## Verification

Unit tests use a fake transport to prove list/card creation, idempotent re-runs, preservation of user card content, and redaction of credentials from output. One opt-in live command validates the configured board once after implementation.
