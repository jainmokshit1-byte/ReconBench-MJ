"""Procrastinate adapter for the application JobQueue port."""

from __future__ import annotations

import asyncio
import socket
from typing import Any
from uuid import UUID

import boto3  # type: ignore[import-untyped]
import sqlalchemy as sa
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError  # type: ignore[import-untyped]
from procrastinate import App, JobContext, PsycopgConnector, SyncPsycopgConnector
from procrastinate.retry import RetryStrategy
from sqlalchemy.engine import Connection, Engine, make_url

from reconbench_app.db.metadata import metadata
from reconbench_app.export_service import ExportService
from reconbench_app.ingestion import (
    IngestionService,
    ObjectStoreUnavailable,
    QueuePort,
)
from reconbench_app.ledger_service import LedgerRebuildService
from reconbench_app.logging_config import configure_logging
from reconbench_app.mapping_service import MappingService
from reconbench_app.reconciliation_service import ReconciliationService
from reconbench_app.settings import Settings
from reconbench_app.storage import S3ObjectStore
from reconbench_app.vendor_resolution import VendorResolutionService
from reconbench_app.worker_control import run_controlled_worker

MAX_INFRASTRUCTURE_ATTEMPTS = 4  # One initial attempt plus three infrastructure retries.
_RETRYABLE_ERRORS = (BotoCoreError, OSError, sa.exc.DBAPIError, ObjectStoreUnavailable)
_INFRASTRUCTURE_RETRY = RetryStrategy(
    max_attempts=MAX_INFRASTRUCTURE_ATTEMPTS,
    exponential_wait=2,
    retry_exceptions=_RETRYABLE_ERRORS,
)


class ProcrastinateJobQueue(QueuePort):
    """Queue tasks on the current PostgreSQL transaction when one is supplied."""

    def __init__(self, tasks: dict[str, Any]) -> None:
        self.tasks = tasks

    def enqueue(self, task_name: str, *, file_id: UUID, connection: Connection) -> int:
        task = self.tasks.get(task_name)
        if task is None:
            raise RuntimeError(f"unknown ingestion task: {task_name}")
        psycopg_connection = connection.connection.driver_connection
        return int(task.configure(connection=psycopg_connection).defer(file_id=str(file_id)))

    def enqueue_recompute_po(
        self,
        *,
        workspace_id: UUID,
        po_document_id: UUID,
        connection: Connection,
    ) -> int:
        task = self.tasks.get("recompute_po")
        if task is None:
            raise RuntimeError("recompute_po task is not registered")
        psycopg_connection = connection.connection.driver_connection
        return int(
            task.configure(connection=psycopg_connection).defer(
                workspace_id=str(workspace_id),
                po_document_id=str(po_document_id),
            )
        )

    def enqueue_dirty_scopes(self, *, workspace_id: UUID, connection: Connection) -> int:
        task = self.tasks.get("drain_dirty_scopes")
        if task is None:
            raise RuntimeError("drain_dirty_scopes task is not registered")
        psycopg_connection = connection.connection.driver_connection
        return int(
            task.configure(connection=psycopg_connection).defer(workspace_id=str(workspace_id))
        )

    def enqueue_reconciliation(self, *, run_id: UUID, connection: Connection) -> int:
        task = self.tasks.get("evaluate_cases")
        if task is None:
            raise RuntimeError("evaluate_cases task is not registered")
        psycopg_connection = connection.connection.driver_connection
        return int(task.configure(connection=psycopg_connection).defer(run_id=str(run_id)))

    def enqueue_export(self, *, export_id: UUID, connection: Connection) -> int:
        task = self.tasks.get("build_export")
        if task is None:
            raise RuntimeError("build_export task is not registered")
        psycopg_connection = connection.connection.driver_connection
        return int(task.configure(connection=psycopg_connection).defer(export_id=str(export_id)))

    def enqueue_pending_recomputes(self, *, workspace_id: UUID, connection: Connection) -> int:
        scopes = metadata.tables["dirty_scope"]
        po_ids = connection.scalars(
            sa.select(scopes.c.scope_id)
            .where(
                scopes.c.workspace_id == workspace_id,
                scopes.c.scope_kind == "po",
                scopes.c.processed_at.is_(None),
            )
            .order_by(scopes.c.first_marked_at, scopes.c.id)
        ).all()
        for po_id in po_ids:
            self.enqueue_recompute_po(
                workspace_id=workspace_id,
                po_document_id=UUID(str(po_id)),
                connection=connection,
            )
        return self.enqueue_dirty_scopes(workspace_id=workspace_id, connection=connection)


def make_s3_client(settings: Settings) -> Any:
    """Create the API/worker object-store client with bounded network timeouts."""

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_api_access_key.get_secret_value(),
        aws_secret_access_key=settings.s3_api_secret_key.get_secret_value(),
        config=Config(
            connect_timeout=2,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )


def register_ingestion_tasks(
    settings: Settings,
    engine: Engine,
    s3_client: Any,
    *,
    worker_process: bool = False,
) -> tuple[App, ProcrastinateJobQueue]:
    """Build the same task registry for API enqueueing and worker execution."""

    libpq_url = make_url(settings.database_url).set(drivername="postgresql")
    conninfo = libpq_url.render_as_string(hide_password=False)
    connector = (
        PsycopgConnector(conninfo=conninfo)
        if worker_process
        else SyncPsycopgConnector(conninfo=conninfo)
    )
    task_app = App(connector=connector)
    task_registry: dict[str, Any] = {}
    queue = ProcrastinateJobQueue(task_registry)
    service = IngestionService(
        engine,
        S3ObjectStore(s3_client, settings.s3_bucket),
        queue,
        ocr_languages=tuple(settings.pdf_ocr_languages.split("+")),
    )
    vendor_resolution = VendorResolutionService(engine)
    mapping_service = MappingService(engine, queue)
    ledger_service = LedgerRebuildService(engine)
    reconciliation_service = ReconciliationService(
        engine,
        process_workers=settings.reconciliation_processes,
    )
    export_service = ExportService(engine, S3ObjectStore(s3_client, settings.s3_bucket))

    @task_app.task(
        name="reconbench.ingest_file",
        queue="ingest",
        retry=_INFRASTRUCTURE_RETRY,
        pass_context=True,
    )
    def ingest_file(context: JobContext, file_id: str) -> None:
        job = context.job
        try:
            service.ingest_file(UUID(file_id), job_id=job.id)
        except _RETRYABLE_ERRORS as exc:
            service._record_infrastructure_failure(
                UUID(file_id),
                attempt=job.attempts,
                max_attempts=MAX_INFRASTRUCTURE_ATTEMPTS,
                error_type=type(exc).__name__,
                job_id=job.id,
            )
            raise

    @task_app.task(
        name="reconbench.parse_file",
        queue="parse",
        retry=_INFRASTRUCTURE_RETRY,
        pass_context=True,
    )
    def parse_file(context: JobContext, file_id: str) -> None:
        job = context.job
        try:
            service.parse_file(UUID(file_id), job_id=job.id)
        except _RETRYABLE_ERRORS as exc:
            service._record_infrastructure_failure(
                UUID(file_id),
                attempt=job.attempts,
                max_attempts=MAX_INFRASTRUCTURE_ATTEMPTS,
                error_type=type(exc).__name__,
                job_id=job.id,
            )
            raise

    @task_app.task(
        name="reconbench.resolve_vendors",
        queue="mapping",
        retry=_INFRASTRUCTURE_RETRY,
        pass_context=True,
    )
    def resolve_vendors(context: JobContext, file_id: str) -> None:
        del context
        source = metadata.tables["source_file"]
        with engine.connect() as connection:
            workspace_id = connection.scalar(
                sa.select(source.c.workspace_id).where(source.c.id == UUID(file_id))
            )
        if workspace_id is not None:
            vendor_resolution.resolve_workspace(UUID(str(workspace_id)))
            with engine.begin() as connection:
                queue.enqueue("link_vendor", file_id=UUID(file_id), connection=connection)

    @task_app.task(
        name="reconbench.link_vendor",
        queue="mapping",
        retry=_INFRASTRUCTURE_RETRY,
        pass_context=True,
    )
    def link_vendor(context: JobContext, file_id: str) -> None:
        del context
        source = metadata.tables["source_file"]
        document = metadata.tables["document"]
        document_version = metadata.tables["document_version"]
        with engine.connect() as connection:
            workspace_id = connection.scalar(
                sa.select(source.c.workspace_id).where(source.c.id == UUID(file_id))
            )
            grn_document_ids = list(
                connection.scalars(
                    sa.select(document.c.id)
                    .select_from(
                        document.join(
                            document_version,
                            document.c.current_version_id == document_version.c.id,
                        )
                    )
                    .where(
                        document.c.workspace_id == workspace_id,
                        document_version.c.source_file_id == UUID(file_id),
                        document.c.doc_type == "grn",
                        document.c.withdrawn_at.is_(None),
                    )
                    .order_by(document.c.id)
                ).all()
            )
        if workspace_id is not None:
            resolved_workspace_id = UUID(str(workspace_id))
            mapping_service.resolve_workspace(resolved_workspace_id)
            with engine.begin() as connection:
                queue.enqueue_pending_recomputes(
                    workspace_id=resolved_workspace_id,
                    connection=connection,
                )
            reconciliation_service.enqueue_pending_grn_rechecks(
                resolved_workspace_id,
                [UUID(str(document_id)) for document_id in grn_document_ids],
                queue,
            )

    @task_app.task(
        name="reconbench.recompute_po",
        queue="mapping",
        retry=_INFRASTRUCTURE_RETRY,
        pass_context=True,
    )
    def recompute_po(
        context: JobContext,
        workspace_id: str,
        po_document_id: str,
    ) -> None:
        del context
        ledger_service.rebuild_po(UUID(workspace_id), UUID(po_document_id))

    @task_app.task(
        name="reconbench.drain_dirty_scopes",
        queue="mapping",
        retry=_INFRASTRUCTURE_RETRY,
        pass_context=True,
    )
    def drain_dirty_scopes(context: JobContext, workspace_id: str) -> None:
        del context
        resolved_workspace_id = UUID(workspace_id)
        while ledger_service.drain_dirty_scopes(resolved_workspace_id)["scopes"]:
            pass

    @task_app.task(
        name="reconbench.evaluate_cases",
        queue="reconcile",
        retry=_INFRASTRUCTURE_RETRY,
        pass_context=True,
    )
    def evaluate_cases(context: JobContext, run_id: str) -> None:
        del context
        reconciliation_service.evaluate_run(UUID(run_id))

    @task_app.task(
        name="reconbench.build_export",
        queue="exports",
        retry=_INFRASTRUCTURE_RETRY,
        pass_context=True,
    )
    def build_export(context: JobContext, export_id: str) -> None:
        del context
        export_service.build_export(UUID(export_id))

    task_registry.update(
        {
            "ingest_file": ingest_file,
            "parse_file": parse_file,
            "resolve_vendors": resolve_vendors,
            "link_vendor": link_vendor,
            "recompute_po": recompute_po,
            "drain_dirty_scopes": drain_dirty_scopes,
            "evaluate_cases": evaluate_cases,
            "build_export": build_export,
        }
    )
    return task_app, queue


def worker_main(
    *,
    fetch_job_polling_interval: float = 5.0,
    update_heartbeat_interval: float = 10.0,
    stalled_worker_timeout: float = 30.0,
    shutdown_graceful_timeout: float = 300.0,
) -> int:
    """Run all application queues with global drain/resume control."""

    from reconbench_app.api.application import create_engine_and_store

    settings = Settings()  # type: ignore[call-arg]  # BaseSettings loads required values from env.
    configure_logging(settings.log_level)
    engine, s3_client = create_engine_and_store(settings)
    app, _queue = register_ingestion_tasks(settings, engine, s3_client, worker_process=True)
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())
    try:
        asyncio.run(
            run_controlled_worker(
                app,
                settings.database_url,
                queues=["ingest", "parse", "mapping", "reconcile", "exports", "ai"],
                name=f"reconbench-ingestion-{socket.gethostname()}-{socket.getfqdn()}",
                fetch_job_polling_interval=fetch_job_polling_interval,
                update_heartbeat_interval=update_heartbeat_interval,
                stalled_worker_timeout=stalled_worker_timeout,
                shutdown_graceful_timeout=shutdown_graceful_timeout,
            )
        )
    finally:
        engine.dispose()
    return 0


__all__ = [
    "MAX_INFRASTRUCTURE_ATTEMPTS",
    "ProcrastinateJobQueue",
    "make_s3_client",
    "register_ingestion_tasks",
    "worker_main",
]
