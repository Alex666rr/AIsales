# Telegram compatibility evidence

Stage 0 records technical compatibility evidence only. A successful row proves
that an injected adapter/version and assigned proxy completed a synthetic test
message operation; it does not permit production messaging, proxy rotation, or
any Telegram-to-AI processing.

`CompatibilityRegistry` records these safe fields:

- adapter identifier and adapter version;
- proxy UUID, never a proxy endpoint or credentials;
- normalized outcome (`sent`, `reconciled`, connection state, or a stable
  error code); and
- an aware UTC timestamp.

It upserts one row per adapter/version/proxy UUID combination, replacing only
the normalized outcome and timestamp on a later observation.

The registry accepts a `ProxyConfig` only to extract its UUID. It cannot store
the proxy URL, username, or password. It does not accept Telegram message text,
phone numbers, account codes, tokens, passwords, or session material.

## Idempotent message gateway

The outbound gateway accepts an active connection predicate, normalized positive
peer ID, idempotency key, and an ephemeral message body. The body lives only in
a gateway-local vault behind an opaque command handle. It is excluded from all
Pydantic serialization and `repr`, and it is passed only to the injected
network client. Rejected command metadata removes the stored body immediately.
Once `send()` returns or raises, one outer `finally` removes the body for every
terminal branch; same-call reconciliation and an authorized resend may retain
it only until that call ends. Delivery rows retain account ID, peer ID,
idempotency key, state, stable error code, and external message ID—not the body.

The PostgreSQL `MessageDeliveryRepository` contract is the source of truth. Its
`reserve` operation is one transaction that persists a pending row before any
send. One sender receives the reservation; duplicate callers wait and return
the same completed result. A failed send is first persisted as `uncertain`.
Before any later resend, the gateway calls the injected reconciliation boundary
with the peer and idempotency key. A discovered remote message completes the
same row; only an atomic reconciled miss can grant a new sender reservation.
No Redis or process-local lock is authoritative.

Remote replacement authority is not a caller-supplied capability. The public
gateway constructor has no decoder or remote-deduplication grant parameters,
and the package exports no authority registry. A private application-composition
path matches the exact registered adapter implementation identity to fixed
capability metadata. Directly constructed gateways therefore fail closed after
an uncertain reconciliation miss. The registered stage-0 adapter still sends
the same idempotency key for every live replacement request, so remote
deduplication, not a local lease, defines the one-effect boundary.

Incoming events are default-deny. The normalized `TelegramUpdate` contains only
an update ID, sender ID, peer ID, and receipt timestamp. Decoder issuance is
private to the same fixed adapter composition. Each gateway tracks only the
opaque envelopes issued by its bound decoder, so direct, forged, and separately
issued envelopes are denied. The classifier accepts only authenticated private,
non-service user messages with positive IDs; it excludes groups, supergroups,
channels, bots, service traffic, and malformed/unknown events. It deliberately
carries no message text and creates no AI path.

## Live evidence procedure

`services/telegram_connector/tests/manual/test_live_roundtrip.py` is disabled
unless `RUN_TELEGRAM_LIVE_TESTS=1`. It additionally requires an ignored,
project-owned `TELEGRAM_LIVE_ROUNDTRIP_FACTORY` fixture. That fixture owns all
credentials and sessions and must provide async send, receive, restart,
compatibility-row, and sender-session archive operations. The test sends one
unique synthetic marker between two project-owned accounts, verifies receipt,
restarts the connector, verifies one reply, then archives the sender session in
`finally` even when an assertion fails. It verifies one compatibility row per
adapter/version/proxy UUID combination.

Never place the factory, credentials, sessions, raw messages, or account
identifiers in version control or test output.

## Durable implementation

`SqlAlchemyMessageDeliveryRepository` is the production PostgreSQL source of
truth. Its migration creates a composite idempotency primary key plus owner,
lease-expiry, and fence-token fields. `reserve` locks a durable row; an expired
pending lease becomes `uncertain` and must reconcile before one atomic resend
reservation. Duplicate callers use bounded database polling, never an
in-process event. Cancellation shields the transition to durable uncertainty
before it escapes the gateway.

`SqlAlchemyCompatibilityRegistry` upserts a composite adapter/version/proxy
primary key. Direct operation uses a non-null empty proxy key so PostgreSQL
cannot create multiple NULL-key rows. The in-memory repository and registry are
explicit test fakes only.

Trusted inbound adapter composition issues opaque gateway-bound envelopes. The
gateway refuses arbitrary public labels such as `peer_kind` and accepts only
authenticated non-service user-to-user envelopes. Outbound body validation uses
`MessageCommand.create`; the command contains only metadata and an opaque UUID
handle, so Pydantic errors, repr, copies, and serialization cannot expose raw
text.
