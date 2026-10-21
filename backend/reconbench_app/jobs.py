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
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.manager import JobManager
from procrastinate.retry import RetryStrategy
from sqlalchemy.engine import Connection, Engine, make_url

from reconbench_app.ai.consensus import ConsensusService
from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.ai.limits import load_limits
from reconbench_app.ai.task_contracts import (
    CONSENSUS_QUEUE,
    RUN_AI_REQUEST,
    RUN_CONSENSUS,
    register_ai_task_declarations,
    run_request_kwargs,
)
from reconbench_app.db.metadata import metadata
from reconbench_app.export_service import ExportService
from reconbench_app.extraction.einvoice_keys import einvoice_keys
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

# The "ai" queue is consumed only by the dedicated AI worker process.
GENERAL_WORKER_QUEUES = ("ingest", "parse", "mapping", "reconcile", "exports")
MAX_INFRASTRUCTURE_ATTEMPTS = 4  # One initial attempt plus three infrastructure retries.
_RETRYABLE_ERRORS = (BotoCoreError, OSError, sa.exc.DBAPIError, ObjectStoreUnavailable)
_INFRASTRUCTURE_RETRY = RetryStrategy(
    max_attempts=MAX_INFRASTRUCTURE_ATTEMPTS,
    exponential_wait=2,
    retry_exceptions=_RETRYABLE_ERRORS,
)


class ProcrastinateJobQueue(QueuePort):
    """Queue tasks on the current PostgreSQL transaction when one is supplied."""

    def __init__(
        self,
        tasks: dict[str, Any],
        *,
        sync_job_manager: JobManager | None = None,
    ) -> None:
        self.tasks = tasks
        self.sync_job_manager = sync_job_manager

    def _defer(
        self,
        task_name: str,
        *,
        connection: Connection,
        task_kwargs: dict[str, str],
        queueing_lock: str | None = None,
    ) -> int:
        task = self.tasks.get(task_name)
        if task is None:
            raise RuntimeError(f"unknown ingestion task: {task_name}")
        if queueing_lock is not None:
            # A waiting job with the same lock reads the latest state when it runs,
            # so a second one would only repeat its work (one per key, not per call).
            waiting = connection.scalar(
                sa.text(
                    "SELECT id FROM procrastinate_jobs "
                    "WHERE queueing_lock = :lock AND status = 'todo' LIMIT 1"
                ),
                {"lock": queueing_lock},
            )
            if waiting is not None:
                return int(waiting)
        psycopg_connection = connection.connection.driver_connection
        if queueing_lock is None:
            deferrer = task.configure(connection=psycopg_connection)
        else:
            deferrer = task.configure(connection=psycopg_connection, queueing_lock=queueing_lock)
        if self.sync_job_manager is not None:
            # Worker tasks run on Procrastinate's async connector, but their
            # services use synchronous SQLAlchemy transactions. Use the
            # connector's sync JobManager for this transaction-bound enqueue.
            deferrer.job_manager = self.sync_job_manager
        if queueing_lock is None:
            return int(deferrer.defer(**task_kwargs))
        try:
            # A savepoint keeps the caller's transaction usable if another
            # transaction queued the same waiting job first.
            with connection.begin_nested():
                return int(deferrer.defer(**task_kwargs))
        except AlreadyEnqueued:
            return 0

    def enqueue(self, task_name: str, *, file_id: UUID, connection: Connection) -> int:
        return self._defer(
            task_name,
            connection=connection,
            task_kwargs={"file_id": str(file_id)},
        )

    def enqueue_recompute_po(
        self,
        *,
        workspace_id: UUID,
        po_document_id: UUID,
        connection: Connection,
    ) -> int:
        return self._defer(
            "recompute_po",
            connection=connection,
            task_kwargs={
                "workspace_id": str(workspace_id),
                "po_document_id": str(po_document_id),
            },
            queueing_lock=f"recompute_po:{workspace_id}:{po_document_id}",
        )

    def enqueue_dirty_scopes(self, *, workspace_id: UUID, connection: Connection) -> int:
        return self._defer(
            "drain_dirty_scopes",
            connection=connection,
            task_kwargs={"workspace_id": str(workspace_id)},
            queueing_lock=f"drain_dirty_scopes:{workspace_id}",
        )

    def enqueue_reconciliation(self, *, run_id: UUID, connection: Connection) -> int:
        return self._defer(
            "evaluate_cases",
            connection=connection,
            task_kwargs={"run_id": str(run_id)},
        )

    def enqueue_export(self, *, export_id: UUID, connection: Connection) -> int:
        return self._defer(
            "build_export",
            connection=connection,
            task_kwargs={"export_id": str(export_id)},
        )

    def enqueue_vision_pages(
        self, *, workspace_id: UUID, document_version_id: UUID, connection: Connection
    ) -> int:
        return self._defer(
            "prepare_vision_pages",
            connection=connection,
            task_kwargs={
                "workspace_id": str(workspace_id),
                "document_version_id": str(document_version_id),
            },
        )

    def enqueue_ai_request(self, *, ai_request_id: UUID, connection: Connection) -> int:
        return self._defer(
            RUN_AI_REQUEST,
            connection=connection,
            task_kwargs=run_request_kwargs(ai_request_id),
        )

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
    sync_job_manager = JobManager(connector.get_sync_connector()) if worker_process else None
    queue = ProcrastinateJobQueue(task_registry, sync_job_manager=sync_job_manager)
    ai_hooks = AiHookEnqueuer(
        gate=settings.deployment_gate(),
        queue=queue,
        limits=lambda: load_limits(settings.ai_limits_file),
        vision_reader_name=settings.vision_reader,
    )
    service = IngestionService(
        engine,
        S3ObjectStore(s3_client, settings.s3_bucket),
        queue,
        ocr_languages=tuple(settings.pdf_ocr_languages.split("+")),
        einvoice_keys=einvoice_keys(settings.einvoice_public_key_file),
        extraction_engine=settings.extraction_engine,
        ai_hooks=ai_hooks,
        ai_limits=lambda: load_limits(settings.ai_limits_file),
    )
    vendor_resolution = VendorResolutionService(engine, ai_hooks)
    mapping_service = MappingService(engine, queue, ai_hooks)
    ledger_service = LedgerRebuildService(engine)
    reconciliation_service = ReconciliationService(
        engine,
        process_workers=settings.reconciliation_processes,
        ai_hooks=ai_hooks,
    )
    export_service = ExportService(engine, S3ObjectStore(s3_client, settings.s3_bucket))

    def enqueue_consensus_followup(
        connection: Connection,
        workspace_id: UUID,
        document_id: UUID,
        source_file_id: UUID,
        doc_type: str,
    ) -> None:
        queue.enqueue("resolve_vendors", file_id=source_file_id, connection=connection)
        if doc_type == "po":
            queue.enqueue_recompute_po(
                workspace_id=workspace_id,
                po_document_id=document_id,
                connection=connection,
            )

    consensus_service = ConsensusService(
        engine,
        ocr_languages=tuple(settings.pdf_ocr_languages.split("+")),
        enqueue_followup=enqueue_consensus_followup,
    )

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
        name="reconbench.prepare_vision_pages",
        queue="parse",
        retry=_INFRASTRUCTURE_RETRY,
        pass_context=True,
    )
    def prepare_vision_pages(
        context: JobContext, workspace_id: str, document_version_id: str
    ) -> None:
        service.prepare_vision_pages(
            UUID(workspace_id), UUID(document_version_id), job_id=context.job.id
        )

    @task_app.task(
        name=RUN_CONSENSUS,
        queue=CONSENSUS_QUEUE,
        retry=_INFRASTRUCTURE_RETRY,
        pass_context=True,
    )
    def process_consensus(context: JobContext, workspace_id: str, consensus_run_id: str) -> None:
        resolved_workspace_id = UUID(workspace_id)
        resolved_run_id = UUID(consensus_run_id)
        try:
            consensus_service._process(resolved_workspace_id, resolved_run_id)
        except _RETRYABLE_ERRORS:
            if context.job.attempts >= MAX_INFRASTRUCTURE_ATTEMPTS:
                consensus_service._fail(
                    resolved_workspace_id, resolved_run_id, "INFRASTRUCTURE_FAILED"
                )
            raise
        except Exception:  # noqa: BLE001 - close transient page and run state on final failure.
            consensus_service._fail(resolved_workspace_id, resolved_run_id, "CONSENSUS_FAILED")
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
            "prepare_vision_pages": prepare_vision_pages,
            "process_consensus": process_consensus,
            "resolve_vendors": resolve_vendors,
            "link_vendor": link_vendor,
            "recompute_po": recompute_po,
            "drain_dirty_scopes": drain_dirty_scopes,
            "evaluate_cases": evaluate_cases,
            "build_export": build_export,
            **register_ai_task_declarations(task_app),
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

    settings = Settings()  # BaseSettings loads required values from env.
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
                queues=list(GENERAL_WORKER_QUEUES),
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
    "GENERAL_WORKER_QUEUES",
    "MAX_INFRASTRUCTURE_ATTEMPTS",
    "ProcrastinateJobQueue",
    "make_s3_client",
    "register_ingestion_tasks",
    "worker_main",
]
