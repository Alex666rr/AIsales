FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/apps/api:/workspace

WORKDIR /workspace

COPY pyproject.toml ./
COPY services ./services
RUN pip install --no-cache-dir .

COPY apps ./apps

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
