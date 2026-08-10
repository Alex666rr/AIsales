# Telegram/AI approval record

## Decision boundary

Real Telegram content is denied by default. Application code first obtains a server-issued, organization-bound `AiOperationContext`, then calls `PolicyGate.require_ai_operation`. No message text, phone number, token, session material, credential, or personal detail is accepted by the policy service or repository. A `PolicyProtectedMessageLoader` invokes its content loader only after an allowed decision.

A real Telegram decision is allowed only when one immutable approval matches all of these fields at PostgreSQL's current UTC time:

- organization UUID;
- channel type (`mtproto_user` or `bot_api`);
- allow-listed data category;
- allow-listed operation (`draft`, `auto_reply`, `summarize`, or `classify`);
- current server-configured terms revision;
- `approved_at <= current_timestamp < expires_at`; and
- no row exists in `ai_approval_revocations`.

Missing, malformed, stale, revoked, or unavailable repository state denies. Synthetic input can bypass the Telegram approval lookup only when the gate is explicitly composed with a separate trusted synthetic authority intended for a test harness. The production Telegram authority cannot issue or relabel synthetic input.

## Authority and history

Policy and owner capabilities are authorized by exact issued-object identity. The issuing authority retains immutable canonical claims server-side and revalidates both identity and every claim on every use. Copying fields, constructing a lookalike object, relabeling the origin, or mutating a frozen object does not confer authority. Request bodies cannot claim a role.

The public approval repository exposes queries only. Approval administration is the mutation boundary and revalidates a `PlatformOwnerPrincipal` immediately before invoking its private database writer. Database functions pair each approval or revocation append with its audit event in one transaction; revocation also locks the approval row. Row triggers reject updates and deletes, statement triggers reject truncation, and the migration revokes direct table mutation plus function execution from `PUBLIC`.

The database function's actor UUID is audit metadata, not an authorization claim. The migration deliberately creates no runtime role and grants no writer role. Deployment must use a separately configured, non-owner runtime role, grant it only the required reads and `EXECUTE` on the two policy write functions, and retain table ownership outside the application runtime. PostgreSQL owners and superusers remain trusted administrative boundaries.

The API returns only an approval UUID and a safe status. It does not return evidence, actor details, audit contents, raw rejected validation input, or raw internal errors. Validation failures use a fixed safe 422 response.

## Approval decision template

Copy this section into the controlled approval system. Do not include Telegram content, phone numbers, usernames, credentials, tokens, session details, or other personal data.

```text
TELEGRAM / AI APPROVAL DECISION

Decision date (UTC):
Organization UUID:
Responsible platform owner UUID:

Terms revisions checked:
- Product terms revision:
- Privacy/data-processing revision:
- Telegram terms/policy revision:

Approved channel types (select only):
- [ ] mtproto_user
- [ ] bot_api

Approved data categories (select only):
- [ ] message_text
- [ ] message_metadata
- [ ] attachment_text
- [ ] voice_transcript

Approved AI operations (select only):
- [ ] draft
- [ ] auto_reply
- [ ] summarize
- [ ] classify

Restrictions and safeguards:
Approval expires at (UTC):
Evidence URI (no embedded credentials or personal data):

Explicit outcome (select exactly one):
- [ ] APPROVE
- [ ] DENY

Decision rationale (content-free):
```

An approval is effective only after an `APPROVE` decision is persisted through the trusted server administration service. A document or role claim alone never authorizes processing.
