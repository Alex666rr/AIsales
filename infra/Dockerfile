FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/apps/api:/workspace

WORKDIR /workspace

COPY pyproject.toml ./
COPY services ./services
COPY apps ./apps
COPY infra/postgres/railway ./infra/postgres/railway
COPY alembic.ini ./alembic.ini
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir .

CMD ["uvicorn", "app.main:create_app_from_environment", "--factory", "--host", "0.0.0.0", "--port", "8000"]
