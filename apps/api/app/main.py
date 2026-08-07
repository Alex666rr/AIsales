"""FastAPI composition root for the test-only prototype."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build the control API without creating external connections at import time."""
    api = FastAPI(title="AI Sales Manager Prototype", version="0.1.0")

    @api.get("/healthz")
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": "prototype"}

    return api


app = create_app()
