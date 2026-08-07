# Local Telegram authorization fixtures

This directory is intentionally ignored except for this file. It is only for
manual, local checks against project-owned test accounts; automated tests use
in-memory fakes and must never load these files or contact Telegram.

Create only the fixture you need, using these exact local filenames:

- `phone-code.txt` -- a current test-account phone code.
- `two-factor-password.txt` -- the matching test-account 2FA password.
- `qr-login-token.txt` -- a QR token created during a manual test.
- `tdata-export.zip` -- a test-account TData export.
- `telethon-user.session` -- a test-account Telethon file session.
- `telethon-string.txt` -- a test-account Telethon string session.
- `bot-token.txt` -- a project-owned test bot token.

Do not put production accounts, customer exports, API credentials, screenshots,
or decrypted session data here. Every file other than this README is ignored
by Git; delete local fixtures after a manual check.
