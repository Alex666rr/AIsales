# AIsales Presentation Release Design

## Purpose

Build a real, controlled vertical slice of AIsales for an internal presentation.
The presentation must demonstrate the actual product path, not a mock-up:

```text
Telegram account → test contacts → campaign → controlled send → incoming reply
→ manager inbox → manual reply → activity and baseline statistics
```

The release is a temporary delivery priority, not a replacement for the MVP
specification in `outputs/AI-Sales-Manager-TZ.md`. Work completed for it must
remain production-quality and continue to serve the full Stage 1 plan.

## Product scope

### Included

1. **Accounts and connectivity (S1.04)**
   - Owner/admin can connect a project-owned test Telegram account through the
     existing phone, QR or approved local `tdata` boundary.
   - The web interface shows account state, proxy assignment/health and a
     human-readable reason for any blocked action.
   - No session material or secret is returned to the browser.

2. **Contacts and consent-safe import (S1.05, S1.06, S1.07)**
   - Import a small CSV/XLSX test list; preview valid, invalid, duplicate and
     suppressed rows before confirmation.
   - Show contacts in a searchable list with independent sales, permission and
     technical states plus immutable history.
   - Permit a selected, explicitly confirmed bulk action only when its result
     is auditable.

3. **Controlled campaign and delivery (S1.08, S1.09)**
   - Create one versioned campaign with one test message, a selected audience,
     one or more assigned test accounts, schedule and per-account limit.
   - Run a real Telegram send through the existing gateway and outbox.
   - Show queued, sent, failed and replied outcomes; prevent duplicate sends
     after a retry or restart.

4. **Manager inbox (S1.10)**
   - An incoming reply appears in a shared queue and opens in a dialogue view.
   - A manager can take ownership and send a manual reply through the same
     policy, audit and idempotency path as a campaign message.
   - Human handoff is explicit; no AI sends messages during this release.

5. **Presentation dashboard and task visibility (narrow S1.11/S1.12)**
   - Overview exposes the next action, account health, campaign state,
     unprocessed replies and recent background jobs.
   - Baseline counters show contacts imported, messages queued/sent/failed,
     replies, handoffs and account/proxy incidents for a selected period.
   - Each long-running action has progress, terminal result and safe error
     text. Internal web notifications cover new replies and critical account
     errors.

6. **UX principles learned from the GramGPT reference**
   - Use an operational control room rather than a menu of unrelated modules.
   - Present preflight requirements before a connect, import, campaign or send.
   - Make each empty state explain what is missing and provide one safe next
     action.
   - Separate business work (Contacts, Campaigns, Inbox) from infrastructure
     (Accounts, Proxies, Integrations).
   - Use status chips, time-stamped activity and a task history. Do not adopt
     the competitor's bulk/spam-oriented modules or navigation model.

### Excluded from the presentation release

- Sending to non-test contacts, high-volume sending, hidden automation or any
  attempt to circumvent Telegram limitations.
- AI-generated or autonomous replies, RAG, customer memory, offers, external
  search, voice, attachment analysis and AI evaluations. These remain Stage 2.
- A/B optimisation, adaptive pacing, cost attribution, payout calculations,
  load testing and full analytics. These remain Stage 3.
- Google Sheets, public API, webhooks and external notification channels. The
  underlying event model must keep them possible for full S1.12 later.
- Public PostgreSQL access. Railway private networking remains the database
  boundary.

## Safety and data boundaries

- Demonstration uses only project-owned Telegram test accounts and contacts
  that explicitly consent to testing.
- The first demonstration campaign is capped at three contacts, one message
  per contact and a low per-account daily limit. These limits are configuration
  values, not a bypassable UI convention.
- All send, retry, cancellation, manual reply and status transitions are
  audited under their organization and actor.
- The worker persists its delivery attempt before Telegram I/O and reconciles
  uncertain outcomes before a retry.
- AI has no permission to read or send real Telegram content in this release.

## Demonstration flow

1. Sign in as owner; show the control room and account health.
2. Connect or show an already-connected test account and its non-secret status.
3. Import a small test contact file; explain preview warnings and confirmation.
4. Create a campaign, pass preflight, set the small limit and approve launch.
5. Show its background job progressing and a real test Telegram message.
6. Reply from the recipient test account; show the new inbox item.
7. Assign the dialogue to the manager and send one manual answer.
8. Return to the overview/statistics screen and show the recorded events.

## Release acceptance criteria

- Every step in the demonstration flow runs against the deployed Railway
  environment with a real test account and no browser-only fake state.
- A campaign cannot launch if account health, audience, permission state,
  schedule or limits fail preflight.
- Re-running a worker or retrying a request does not create a second business
  send for the same campaign-contact touch.
- A reply cancels future touches for that contact and creates an inbox item.
- An owner can identify the actor, time and outcome of each demonstration
  action from the activity/audit surface.
- The product is presentable without exposing API credentials, sessions,
  database strings, recovery codes or Telegram secrets.

## Delivery order

The work remains dependency-driven rather than strictly numbered by original
stage:

1. S1.04 accounts, proxies and health UI.
2. S1.05 import and S1.07 status model; add only the S1.06 bulk confirmation
   required to select a small test audience.
3. S1.08 campaign wizard and S1.09 outbox/scheduler with presentation caps.
4. S1.10 inbox, handoff and manual reply.
5. Narrow S1.11/S1.12 dashboard, internal alerts, job history and baseline
   counters.
6. End-to-end rehearsal, recovery check and a short presenter runbook.

## Trello operating rule

Trello is the live progress board. A routine status update must be one direct
board update after a completed product block; it must not require a dedicated
branch, PR or CI run. The repository synchronizer may create or refresh card
structure, but must preserve an existing live card status unless explicitly
asked to reset the board.
