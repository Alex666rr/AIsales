# Stage 1: Organizations, Roles, and 2FA Design

## Goal

Provide tenant-safe user access for the sales platform without public
registration. The configured platform owner provisions organizations and users;
users authenticate with a password and complete mandatory TOTP for privileged
roles.

## Roles and Provisioning

The platform owner is configured outside user-controlled request bodies. It can
create an organization and invite its first company owner. A company owner can
invite administrators and managers only inside its organization. No endpoint
accepts a caller-supplied role as proof of authority.

Roles are `platform_owner`, `company_owner`, `administrator`, and `manager`.
Permissions are explicitly checked at service boundaries. A request for a
different tenant's resource returns a neutral not-found result.

## Authentication Flow

1. A provisioned user submits email and password.
2. Password verification yields either a TOTP enrollment requirement or a
   short-lived 2FA challenge.
3. A valid TOTP code completes the authenticated session.
4. Privileged roles cannot bypass enrollment, even after password success.
5. Sessions are server-side, revocable, auditable, and never stored in browser
   local storage.

## Data and Security

- Passwords use an adaptive one-way hash; plaintext is never stored or logged.
- TOTP secrets are encrypted at rest and never returned after enrollment.
- Recovery codes are stored only as hashes and consumed once.
- Session records include user, organization, creation, last activity, expiry,
  and revocation fields.
- Cross-tenant reads and writes fail closed before returning resource details.

## Verification

Tests cover permission matrices for all four roles, mandatory 2FA, invalid
passwords and TOTP codes, recovery-code single use, session revocation, and
cross-tenant neutral not-found results. Focused tests run during development;
one full suite runs before the completed auth-block commit.
