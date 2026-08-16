"""FastAPI composition root for the test-only prototype."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def safe_request_validation_error_handler(
    _request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    """Return fixed validation output without serializing or logging rejected input."""
    return JSONResponse(status_code=422, content={"detail": "request validation failed"})


def create_app(*, composition=None) -> FastAPI:
    """Build the control API without creating external connections at import time."""
    @asynccontextmanager
    async def lifespan(_api: FastAPI):
        yield
        if composition is not None:
            await composition.close()

    api = FastAPI(
        title="AI Sales Manager Prototype",
        version="0.1.0",
        lifespan=lifespan,
    )
    api.add_exception_handler(RequestValidationError, safe_request_validation_error_handler)

    @api.get("/healthz")
    async def health_check() -> JSONResponse:
        try:
            ready = composition is not None and await run_in_threadpool(
                composition.database_is_ready
            )
        except Exception:
            ready = False
        if not ready:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "environment": "prototype"},
        )

    if composition is not None:
        api.include_router(composition.policy_router)
        api.include_router(composition.connection_router)
        api.include_router(composition.tdata_router)
        api.state.composition = composition

    return api


def create_app_from_environment() -> FastAPI:
    """Uvicorn factory that fails startup when production dependencies are incomplete."""
    from .composition import create_production_composition

    return create_app(composition=create_production_composition())
