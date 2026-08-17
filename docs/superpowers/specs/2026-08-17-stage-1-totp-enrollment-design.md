# Stage 1: TOTP Enrollment and Recovery-Code Design

## Goal

Let a newly activated privileged user enroll a TOTP authenticator and receive one-time recovery codes without storing an unencrypted TOTP secret, reusing the setup token, or issuing a session before second-factor verification.

## Flow

1. `POST /auth/setup` consumes the first-owner setup token, stores the password hash, and creates a short-lived enrollment challenge.
2. Its one-time response contains an opaque enrollment token and an `otpauth://` URI. The user scans the URI with a TOTP authenticator.
3. `POST /auth/totp/confirm` accepts the enrollment token and a six-digit code. The service verifies the code against the pending encrypted secret.
4. On success, the service marks the challenge consumed, retains the encrypted secret on the user, creates a fixed recovery-code set as hashes, and returns the raw recovery codes once.
5. A future login requires a valid TOTP code or one unused recovery code. The service never issues a privileged session before enrollment completes.

## Data and Security

- The pending TOTP secret uses the existing AES-GCM `auth-totp-v1` envelope before and after confirmation; plaintext exists only while generating the URI and verifying the code.
- Enrollment tokens use a public UUID prefix and random secret suffix. PostgreSQL stores only the one-way hash, expiry and consumed timestamp.
- The setup token remains one-time and is not accepted by the TOTP confirmation endpoint.
- Recovery codes are random values stored only as existing one-way hashes. Their raw values appear only in the successful confirmation response and are excluded from model representations.
- Invalid, expired and consumed enrollment tokens return the same safe authorization failure. API responses and exceptions never echo token, URI, TOTP secret or recovery codes.

## Boundaries

- `ProvisioningService` creates the enrollment challenge after setup activation.
- `AuthService` owns confirmation and recovery-code generation because it owns encrypted TOTP material and privileged login.
- HTTP routes remain thin: `/auth/setup` starts enrollment; `/auth/totp/confirm` finalizes it.
- The platform-owner provisioning endpoint remains unchanged.

## Verification

- TDD covers expiry, one-time challenge consumption, malformed/wrong codes, secret redaction, one-time recovery-code use and privileged-login denial before confirmation.
- The final block runs auth, organization and composition tests, full pytest, Alembic SQL rendering and GitHub Actions after commit/push.
