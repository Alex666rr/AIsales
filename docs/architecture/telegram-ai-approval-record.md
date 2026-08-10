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

Missing, malformed, stale, revoked, or unavailable repository state denies. Only explicitly server-issued synthetic input bypasses the Telegram approval lookup.

## Authority and history

Approval administration requires an opaque `PlatformOwnerPrincipal` minted by the server's `PlatformOwnerAuthority`. Request bodies cannot claim a role. Creation appends the grant and a `created` audit event in one transaction. Revocation locks the grant and appends one separate revocation plus one `revoked` audit event in one transaction. Database triggers reject updates and deletes to all three history tables.

The API returns only an approval UUID and a safe status. It does not return evidence, actor details, audit contents, or raw internal errors.

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
