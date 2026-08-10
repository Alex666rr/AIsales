"""FastAPI composition root for the test-only prototype."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def safe_request_validation_error_handler(
    _request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    """Return fixed validation output without serializing or logging rejected input."""
    return JSONResponse(status_code=422, content={"detail": "request validation failed"})


def create_app() -> FastAPI:
    """Build the control API without creating external connections at import time."""
    api = FastAPI(title="AI Sales Manager Prototype", version="0.1.0")
    api.add_exception_handler(RequestValidationError, safe_request_validation_error_handler)

    @api.get("/healthz")
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": "prototype"}

    return api


app = create_app()
