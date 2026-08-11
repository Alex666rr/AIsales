# Railway Dockerfile Reliability Design

## Purpose

Make both Railway services build from the reviewed `infra/Dockerfile`, rather than allowing Railpack to infer a Python startup command.

## Evidence

The failed API deployments used build driver `railpack-v0.36.0` and stopped with `No start command detected`. The Dockerfile is outside the repository root. Inspection of the current `main` confirms it already copies `infra/postgres/railway`, the exact migration script directory needed inside the image.

## Design

1. Add root `railway.json` that explicitly selects the Dockerfile builder and `/infra/Dockerfile`. Railway therefore applies the same reviewed image contract to the API and one-shot migrations services.
2. Add a focused regression test that asserts the Railway config selects Dockerfile and declares the expected path.

## Boundaries

No Dockerfile, runtime secret, database value, Telegram value, source-service wiring, or migration SQL changes. Railway dashboard start commands stay service-specific: the API uses the existing Dockerfile `CMD`, while the migrations service keeps its existing one-shot command.

## Verification

Run the new regression test, the complete test suite, syntax/format checks, and inspect the built configuration. Then open a PR; Railway will rebuild from the merged `main` branch.
