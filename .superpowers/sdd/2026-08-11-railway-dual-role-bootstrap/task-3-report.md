# Task 3 report: Railway deployment handoff

## Change

Added a first-deployment checklist to `docs/deployment/railway-stage-0.md` and
a regression test in `services/telegram_connector/tests/test_railway_bootstrap.py`.
The checklist requires the `Postgres → Migrations → AIsales` order, a review
of staged Railway changes, healthy Postgres, successful Alembic output, and a
healthy `/healthz` response. It blocks deployment on exposed secrets or
role/bootstrap/Alembic failures. No Railway service was changed and no
deployment was initiated.

## TDD evidence

The new focused test was run before the guide change and failed as expected:

```powershell
& 'C:\Users\admin\Documents\Codex\2026-08-06\AIsales\.worktrees\stage-0-telegram-prototype\.venv\Scripts\python.exe' -m pytest services/telegram_connector/tests/test_railway_bootstrap.py::test_deployment_guide_requires_a_safe_first_deploy_handoff -q
```

It failed because `Do not click Deploy until` was absent. After the guide was
updated, the same command passed (`1 passed`).

Independent review identified that the initial test checked only two specific
placeholder formats and did not protect the secret-log stop condition. The
test now rejects any `TELEGRAM_API_ID` or `TELEGRAM_API_HASH` assignment and
requires the secret-log stop. Removing that stop condition made the focused
test fail as expected; restoring the guide made it pass.

Fix round 1 added assertions for the staged-change review, healthy Postgres,
successful Alembic output and migration exit, the `/healthz` readiness check,
and both Telegram variables in the `AIsales` configuration table. Removing the
staged-change review made the focused test fail as expected; restoring it made
the test pass.

## Verification

```powershell
& 'C:\Users\admin\Documents\Codex\2026-08-06\AIsales\.worktrees\stage-0-telegram-prototype\.venv\Scripts\python.exe' -m pytest -q --basetemp .pytest-tmp\task3-full
& 'C:\Users\admin\Documents\Codex\2026-08-06\AIsales\.worktrees\stage-0-telegram-prototype\.venv\Scripts\python.exe' -m compileall -q services apps
git diff --check
```

Results: `228 passed, 4 skipped`; compilation and whitespace checks exited 0.
The skips are Docker-dependent checks on a host without Docker.

## Self-review

- The guide includes the explicit pre-deploy gate and required service order.
- It does not include literal Telegram credential examples.
- It requires success signals before advancing and explicit stop conditions.
- The regression test rejects literal Telegram assignments and removal of the
  secret-log stop condition.
- It covers every required deploy-handoff checkpoint and the documented
  Telegram-variable placement.
- The API remains prohibited from receiving owner or administrative database
  credentials.
