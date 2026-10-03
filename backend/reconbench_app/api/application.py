"""FastAPI application factory and Phase 1 health/readiness endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from pydantic.json_schema import models_json_schema
from reconbench_core.schema import CaseResult, LineResult
from sqlalchemy import create_engine, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from reconbench_app.api.ai import router as ai_router
from reconbench_app.api.auth import AuthSessionMiddleware
from reconbench_app.api.auth import router as auth_router
from reconbench_app.api.configuration import router as configuration_router
from reconbench_app.api.conventions import ApiProblem, problem_response
from reconbench_app.api.conventions import validation_errors as convention_validation_errors
from reconbench_app.api.exports import router as exports_router
from reconbench_app.api.ingestion import router as ingestion_router
from reconbench_app.api.mapping import router as mapping_router
from reconbench_app.api.reconciliation import router as reconciliation_router
from reconbench_app.api.review import router as review_router
from reconbench_app.authentication import AuthService
from reconbench_app.authorization import public_endpoint, requires
from reconbench_app.configuration import ConfigurationService
from reconbench_app.db.repository import VersionConflict
from reconbench_app.idempotency import IdempotencyInProgress, IdempotencyKeyConflict
from reconbench_app.ingestion import IngestionService
from reconbench_app.jobs import make_s3_client, register_ingestion_tasks
from reconbench_app.json_serialization import DecimalJSONResponse
from reconbench_app.logging_config import configure_logging
from reconbench_app.mapping_service import MappingService
from reconbench_app.middleware import (
    SECURITY_HEADERS,
    JsonBodyLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from reconbench_app.reconciliation_service import ReconciliationService
from reconbench_app.settings import Settings
from reconbench_app.storage import S3ObjectStore

logger = structlog.get_logger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def create_engine_and_store(settings: Settings) -> tuple[Any, Any]:
    """Create the shared database engine and bounded S3 client for API or worker use."""

    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=5)
    return engine, make_s3_client(settings)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API app; missing required infrastructure configuration fails fast."""

    resolved_settings = settings or Settings()
    configure_logging(resolved_settings.log_level)
    engine, s3 = create_engine_and_store(resolved_settings)
    queue_app, job_queue = register_ingestion_tasks(resolved_settings, engine, s3)
    file_storage = S3ObjectStore(s3, resolved_settings.s3_bucket)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        app.state.database_engine = engine
        app.state.object_store = s3
        app.state.file_storage = file_storage
        yield
        queue_app.close()
        engine.dispose()

    app = FastAPI(
        title="ReconBench API",
        description="ReconBench API for ingestion, authentication, settings, and reconciliation.",
        version="0.1.0",
        default_response_class=DecimalJSONResponse,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        swagger_ui_oauth2_redirect_url="/api/v1/docs/oauth2-redirect",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(AuthSessionMiddleware)
    app.add_middleware(JsonBodyLimitMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.state.auth_service = AuthService(engine, resolved_settings)
    app.state.configuration_service = ConfigurationService(engine)
    app.state.ingestion_service = IngestionService(
        engine,
        file_storage,
        job_queue,
        ocr_languages=tuple(resolved_settings.pdf_ocr_languages.split("+")),
    )
    app.state.mapping_service = MappingService(engine, job_queue)
    app.state.reconciliation_service = ReconciliationService(
        engine,
        process_workers=resolved_settings.reconciliation_processes,
    )
    app.state.job_queue_app = queue_app
    app.state.job_queue = job_queue

    @app.exception_handler(StarletteHTTPException)
    async def problem_details(request: Request, exc: StarletteHTTPException) -> DecimalJSONResponse:
        status = exc.status_code
        detail = (
            exc.detail if isinstance(exc.detail, str) else "The request could not be processed."
        )
        code = "not_found" if status == 404 else "request_error"
        return problem_response(
            status=status,
            title="Not found" if status == 404 else "Request error",
            detail=detail,
            instance=request.url.path,
            code=code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_problem_details(
        request: Request, exc: RequestValidationError
    ) -> DecimalJSONResponse:
        return problem_response(
            status=422,
            title="Invalid request",
            detail="One or more request fields are invalid.",
            instance=request.url.path,
            code="invalid_request",
            errors=convention_validation_errors(exc.errors()),
        )

    @app.exception_handler(ApiProblem)
    async def api_problem_details(request: Request, exc: ApiProblem) -> DecimalJSONResponse:
        return problem_response(
            status=exc.status,
            title=exc.title,
            detail=exc.detail,
            instance=request.url.path,
            code=exc.code,
            errors=exc.errors,
            extensions=exc.extensions,
            headers=exc.headers,
        )

    @app.exception_handler(VersionConflict)
    async def version_conflict_details(
        request: Request, exc: VersionConflict
    ) -> DecimalJSONResponse:
        if exc.current is None:
            return problem_response(
                status=404,
                title="Not found",
                detail="The requested resource was not found.",
                instance=request.url.path,
                code="not_found",
            )
        return problem_response(
            status=409,
            title="Resource changed",
            detail="This resource changed after it was loaded. Reload the current state.",
            instance=request.url.path,
            code="version_conflict",
            extensions={"current": exc.current},
        )

    @app.exception_handler(IdempotencyKeyConflict)
    async def idempotency_key_conflict(
        request: Request, exc: IdempotencyKeyConflict
    ) -> DecimalJSONResponse:
        return problem_response(
            status=409,
            title="Idempotency key conflict",
            detail="This Idempotency-Key was already used for a different request.",
            instance=request.url.path,
            code="idempotency_key_reused",
        )

    @app.exception_handler(IdempotencyInProgress)
    async def idempotency_request_in_progress(
        request: Request, exc: IdempotencyInProgress
    ) -> DecimalJSONResponse:
        return problem_response(
            status=409,
            title="Request in progress",
            detail="The request with this Idempotency-Key is still processing.",
            instance=request.url.path,
            code="idempotency_in_progress",
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(Exception)
    async def unexpected_problem_details(request: Request, exc: Exception) -> DecimalJSONResponse:
        logger.error("unhandled_request_error", error_type=type(exc).__name__)
        return problem_response(
            status=500,
            title="Internal server error",
            detail="The server could not complete the request.",
            instance=request.url.path,
            code="internal_error",
            headers=SECURITY_HEADERS,
        )

    @app.get("/healthz", include_in_schema=True, tags=["Operations"])
    @public_endpoint
    def healthz() -> dict[str, str]:
        """Liveness check with no infrastructure or configuration detail."""

        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=True, tags=["Operations"], response_model=None)
    @public_endpoint
    def readyz() -> DecimalJSONResponse:
        """Readiness checks PostgreSQL, migration head, object storage and a live worker."""

        checks: dict[str, Any] = {}
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                current_heads = set(MigrationContext.configure(connection).get_current_heads())
            scripts = ScriptDirectory.from_config(AlembicConfig(str(BACKEND_ROOT / "alembic.ini")))
            expected_heads = set(scripts.get_heads())
            checks["database"] = True
            checks["migrations"] = current_heads == expected_heads
            if not checks["migrations"]:
                logger.warning(
                    "readiness_check_failed",
                    dependency="migrations",
                    current_head_count=len(current_heads),
                    expected_head_count=len(expected_heads),
                )
        except Exception as exc:  # noqa: BLE001 - return a stable, non-sensitive readiness response.
            checks["database"] = False
            checks["migrations"] = False
            logger.warning(
                "readiness_check_failed",
                dependency="database_migrations",
                error_type=type(exc).__name__,
            )
        try:
            with engine.connect() as connection:
                worker_seen = connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM procrastinate_workers "
                        "WHERE last_heartbeat >= now() - interval '60 seconds')"
                    )
                )
            checks["worker"] = bool(worker_seen)
        except Exception as exc:  # noqa: BLE001 - queue schema/worker failures are non-sensitive.
            checks["worker"] = False
            logger.warning(
                "readiness_check_failed", dependency="worker", error_type=type(exc).__name__
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
            return DecimalJSONResponse(content={"status": "ready"})
        return DecimalJSONResponse(status_code=503, content={"status": "not_ready"})

    api_v1 = APIRouter(prefix="/api/v1", tags=["API v1"])
    api_v1.include_router(auth_router)
    api_v1.include_router(ai_router)
    api_v1.include_router(configuration_router)
    api_v1.include_router(ingestion_router)
    api_v1.include_router(mapping_router)
    api_v1.include_router(reconciliation_router)
    api_v1.include_router(review_router)
    api_v1.include_router(exports_router)
    app.include_router(api_v1)

    # FastAPI's generated OpenAPI/docs endpoints are not public; keep them behind
    # the same authenticated-by-default boundary as the application API.
    for route in app.routes:
        if getattr(route, "path", None) in {
            "/api/v1/openapi.json",
            "/api/v1/docs",
            "/api/v1/docs/oauth2-redirect",
        }:
            requires("data.view")(route.endpoint)

    def openapi_with_problem_media_type() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            openapi_version=app.openapi_version,
            tags=app.openapi_tags,
            servers=app.servers,
        )
        _, reconciliation_models = models_json_schema(
            [(CaseResult, "serialization"), (LineResult, "serialization")],
            ref_template="#/components/schemas/{model}",
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components.update(reconciliation_models.get("$defs", {}))
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                responses = operation.get("responses", {})
                for response in responses.values():
                    content = response.get("content", {})
                    json_content = content.get("application/json", {})
                    response_schema = json_content.get("schema", {})
                    if response_schema.get("$ref") == "#/components/schemas/ProblemDetails":
                        content["application/problem+json"] = content.pop("application/json")
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_with_problem_media_type
    return app
