# Web Control-Room Design

## Goal

Turn the owner web shell into a dark, calm operational interface for daily
administration of Telegram accounts and staff without changing API semantics or
weakening security controls.

## Approved Direction

The owner selected the dark control-room comparison. The interface uses
blue-graphite tonal layers, violet primary actions and reserved semantic status
colours. It must be comfortable in long sessions and not look like a crypto
dashboard.

## Scope

1. Establish root design context in PRODUCT.md and DESIGN.md.
2. Introduce reusable CSS tokens and primitives in the web client.
3. Restyle existing login, owner shell, staff invitation and setup wizard.
4. Add only states supported by the existing API: loading, unavailable,
   authentication failure, invitation success and TOTP setup.
5. Build three representative surfaces: login, owner overview and a Telegram
   connection entry state. The latter stays an entry/empty state until its API
   is mounted.

## Non-goals

- No change to authentication, organisation, Telegram or Railway behaviour.
- No external design export containing production data, credentials, QR payloads
  or setup tokens.
- No light theme in this block.
- No fabricated analytics, customers, account activity or sales results.

## Quality Gates

- Preserve React component tests and add assertions for changed visible states.
- Run pnpm web tests and production build.
- Inspect desktop and mobile in one bounded visual pass.
- Verify contrast, keyboard focus and sensitive-data handling.
