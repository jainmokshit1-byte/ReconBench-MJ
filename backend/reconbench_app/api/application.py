"""FastAPI application factory and Phase 1 health/readiness endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import boto3
import structlog
from botocore.config import Config
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from reconbench_app.logging_config import configure_logging
from reconbench_app.middleware import JsonBodyLimitMiddleware, RequestIdMiddleware
from reconbench_app.settings import Settings

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API app; missing required infrastructure configuration fails fast."""

    resolved_settings = settings or Settings()
    configure_logging(resolved_settings.log_level)
    engine = create_engine(resolved_settings.database_url, pool_pre_ping=True, pool_size=5)
    s3 = boto3.client(
        "s3",
        endpoint_url=resolved_settings.s3_endpoint_url,
        region_name=resolved_settings.s3_region,
        aws_access_key_id=resolved_settings.s3_api_access_key.get_secret_value(),
        aws_secret_access_key=resolved_settings.s3_api_secret_key.get_secret_value(),
        config=Config(
            connect_timeout=2,
            read_timeout=2,
            retries={"max_attempts": 1, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        app.state.database_engine = engine
        app.state.object_store = s3
        yield
        engine.dispose()

    app = FastAPI(
        title="ReconBench API",
        description="Phase 1 API skeleton; product endpoints arrive in later phases.",
        version="0.1.0",
        openapi_url="/openapi.json",
        docs_url="/docs/api",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(JsonBodyLimitMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(StarletteHTTPException)
    async def problem_details(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = exc.status_code
        detail = (
            exc.detail if isinstance(exc.detail, str) else "The request could not be processed."
        )
        code = "not_found" if status == 404 else "request_error"
        return JSONResponse(
            status_code=status,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Not found" if status == 404 else "Request error",
                "status": status,
                "detail": detail,
                "instance": request.url.path,
                "code": code,
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_problem_details(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del exc  # Do not echo submitted values; payloads can contain personal or financial data.
        return JSONResponse(
            status_code=422,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Invalid request",
                "status": 422,
                "detail": "One or more request fields are invalid.",
                "instance": request.url.path,
                "code": "invalid_request",
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_problem_details(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_request_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Internal server error",
                "status": 500,
                "detail": "The server could not complete the request.",
                "instance": request.url.path,
                "code": "internal_error",
            },
        )

    @app.get("/healthz", include_in_schema=True, tags=["Operations"])
    def healthz() -> dict[str, str]:
        """Liveness check with no infrastructure or configuration detail."""

        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=True, tags=["Operations"], response_model=None)
    def readyz() -> JSONResponse:
        """Readiness is successful only when PostgreSQL and the S3 endpoint respond."""

        checks: dict[str, Any] = {}
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception as exc:  # noqa: BLE001 - return a stable, non-sensitive readiness response.
            checks["database"] = False
            logger.warning(
                "readiness_check_failed", dependency="database", error_type=type(exc).__name__
            )
        try:
            s3.list_buckets()
            checks["object_store"] = True
        except Exception as exc:  # noqa: BLE001 - return a stable, non-sensitive readiness response.
            checks["object_store"] = False
            logger.warning(
                "readiness_check_failed", dependency="object_store", error_type=type(exc).__name__
            )

        if all(checks.values()):
            return JSONResponse(content={"status": "ready"})
        return JSONResponse(status_code=503, content={"status": "not_ready"})

    api_v1 = APIRouter(prefix="/api/v1", tags=["API v1"])
    app.include_router(api_v1)
    return app
