# Railway Dockerfile Reliability Design

## Purpose

Make both Railway services build from the reviewed `infra/Dockerfile`, rather than allowing Railpack to infer a Python startup command.

## Evidence

The failed API deployments used build driver `railpack-v0.36.0` and stopped with `No start command detected`. The Dockerfile lives outside the repository root. It also did not copy the `infra` directory, while the migrations command needs `infra/postgres/railway/bootstrap_roles.sh` inside the image.

## Design

1. Add root `railway.json` that explicitly selects the Dockerfile builder and `/infra/Dockerfile`. Railway therefore applies the same reviewed image contract to the API and one-shot migrations services.
2. Copy `infra` into `/workspace/infra` in `infra/Dockerfile`, so the one-shot migration command has its bootstrap script in the built image.
3. Add a focused regression test that asserts the Railway config selects Dockerfile, the expected path is declared, and the Dockerfile copies `infra`.

## Boundaries

No runtime secrets, database values, Telegram values, source-service wiring, or migration SQL change. Railway dashboard start commands stay service-specific: the API uses the Dockerfile `CMD`, while the migrations service keeps its existing one-shot command.

## Verification

Run the new regression test, the complete test suite, syntax/format checks, and inspect the built configuration. Then open a PR; Railway will rebuild from the merged `main` branch.
