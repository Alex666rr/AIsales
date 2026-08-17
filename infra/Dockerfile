FROM node:24-slim AS web-build

WORKDIR /workspace/apps/web
RUN corepack enable
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web ./
RUN pnpm build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/apps/api:/workspace

WORKDIR /workspace

COPY pyproject.toml ./
COPY services ./services
COPY apps ./apps
COPY --from=web-build /workspace/apps/web/dist ./apps/web/dist
COPY infra/postgres/railway ./infra/postgres/railway
COPY alembic.ini ./alembic.ini
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir .

CMD ["sh", "-c", "exec uvicorn app.main:create_app_from_environment --factory --host 0.0.0.0 --port \"${PORT:-8000}\""]
