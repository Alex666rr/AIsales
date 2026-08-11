FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/apps/api:/workspace

WORKDIR /workspace

COPY pyproject.toml ./
COPY services ./services
COPY apps ./apps
COPY alembic.ini ./alembic.ini
RUN pip install --no-cache-dir .

CMD ["uvicorn", "app.main:create_app_from_environment", "--factory", "--host", "0.0.0.0", "--port", "8000"]
