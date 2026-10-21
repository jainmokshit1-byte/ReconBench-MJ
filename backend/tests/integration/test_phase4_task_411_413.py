"""Disposable-database acceptance for Phase 4.11 and 4.13."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from reconbench_app.authorization import ActorContext
from reconbench_app.configuration import canonical_hash
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.seed_data import DEFAULT_CONFIG, REASON_CODES
from reconbench_app.ingestion import IngestionService
from reconbench_app.jobs import make_s3_client, register_ingestion_tasks
from reconbench_app.ledger_service import LedgerRebuildService
from reconbench_app.mapping_service import MappingService
from reconbench_app.reconciliation_service import ReconciliationService
from reconbench_app.settings import Settings
from reconbench_app.storage import LocalObjectStore
from reconbench_app.vendor_resolution import VendorResolutionService
from reconbench_app.worker_control import drain_worker_processes, resume_worker_processes
from reconbench_core.cli import main as core_cli_main
from reconbench_core.recon.engine import partition_components
from reconbench_core.schema import FileFormat, UploadZone
from reconbench_core.version import ENGINE_VERSION
from sqlalchemy.engine import Engine, make_url

ROOT = Path(__file__).resolve().parents[3]
_SAFE_DATABASE_NAME = re.compile(r"^reconbench_phase4_gate_[0-9a-f]{12}$")
_VENDOR_NAME = "Synthetic Accuracy Fixtures Pvt Ltd"
_GSTIN = "27AAPFU0939F1ZV"
_HSN = "8471"


@pytest.fixture(scope="module")
def scratch_engine() -> Iterator[Engine]:
    source_dsn = (
        os.environ.get("RB_PHASE4_TEST_DATABASE_URL", "").strip()
        or os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    )
    if not source_dsn:
        pytest.skip("RB_PHASE4_TEST_DATABASE_URL or RB_PHASE2_TEST_DATABASE_URL is required")

    source_url = make_url(source_dsn)
    source_name = source_url.database or ""
    if not (
        source_name.startswith("reconbench_phase4") or source_name.startswith("reconbench_phase2_")
    ):
        raise RuntimeError("Phase 4.11/4.13 tests require a disposable Phase 2/4 database URL")

    admin_url = source_url.set(database="postgres")
    admin_engine = sa.create_engine(admin_url, pool_pre_ping=True)
    database_name = f"reconbench_phase4_gate_{uuid4().hex[:12]}"
    if not _SAFE_DATABASE_NAME.fullmatch(database_name):
        raise RuntimeError("refusing to create a database with an unexpected scratch name")
    try:
        with admin_engine.connect() as connection:
            server_version = int(connection.scalar(sa.text("SHOW server_version_num")))
            privileges = connection.execute(
                sa.text(
                    "SELECT role.rolcreatedb, role.rolsuper "
                    "FROM pg_roles AS role WHERE role.rolname = current_user"
                )
            ).one()
            if server_version // 10_000 != 16:
                raise RuntimeError("Phase 4.11/4.13 tests require PostgreSQL 16")
            if not (privileges.rolcreatedb or privileges.rolsuper):
                raise RuntimeError(
                    "test database role must have CREATEDB for a disposable scratch DB"
                )
            active_connections = int(
                connection.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = :source_name AND pid <> pg_backend_pid()"
                    ),
                    {"source_name": source_name},
                )
                or 0
            )
            if active_connections:
                raise RuntimeError(
                    "the disposable Phase 2/4 source database has active sessions; "
                    "cannot safely clone it for the Phase 4 acceptance run"
                )
            connection.commit()
            quoted_name = admin_engine.dialect.identifier_preparer.quote(database_name)
            quoted_source = admin_engine.dialect.identifier_preparer.quote(source_name)
            connection.execution_options(isolation_level="AUTOCOMMIT").exec_driver_sql(
                f"CREATE DATABASE {quoted_name} TEMPLATE {quoted_source}"
            )

        scratch_url = source_url.set(database=database_name)
        engine = sa.create_engine(scratch_url, pool_size=8, max_overflow=8)
        try:
            with engine.connect() as connection:
                revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            if revision not in {
                "0015",
                "0016",
                "0017",
                "0018",
                "0019",
                "0020",
                "0021",
                "0022",
                "0023",
                "0024",
                "0025",
                "0026",
                "0027",
                "0028",
                "0029",
                "0030",
            }:
                raise RuntimeError(
                    "Phase 4.11/4.13 source database must be at Alembic revision 0015 to 0030"
                )
            if revision != "0030":
                previous_migrator_url = os.environ.get("RB_MIGRATOR_DATABASE_URL")
                os.environ["RB_MIGRATOR_DATABASE_URL"] = scratch_url.render_as_string(
                    hide_password=False
                )
                try:
                    command.upgrade(Config(str(ROOT / "backend" / "alembic.ini")), "head")
                finally:
                    if previous_migrator_url is None:
                        os.environ.pop("RB_MIGRATOR_DATABASE_URL", None)
                    else:
                        os.environ["RB_MIGRATOR_DATABASE_URL"] = previous_migrator_url
            with engine.begin() as connection:
                tables = list(
                    connection.scalars(
                        sa.text(
                            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                            "AND tablename NOT IN ('alembic_version', 'worker_control') "
                            "ORDER BY tablename"
                        )
                    )
                )
                if "procrastinate_jobs" not in tables:
                    raise RuntimeError("the disposable source database lacks the queue schema")
                quoted_tables = ", ".join(
                    engine.dialect.identifier_preparer.quote(str(table)) for table in tables
                )
                if quoted_tables:
                    for table in tables:
                        quoted_table = engine.dialect.identifier_preparer.quote(str(table))
                        connection.exec_driver_sql(
                            f"ALTER TABLE public.{quoted_table} DISABLE TRIGGER USER"
                        )
                    connection.exec_driver_sql(
                        f"TRUNCATE TABLE {quoted_tables} RESTART IDENTITY CASCADE"
                    )
                    for table in tables:
                        quoted_table = engine.dialect.identifier_preparer.quote(str(table))
                        connection.exec_driver_sql(
                            f"ALTER TABLE public.{quoted_table} ENABLE TRIGGER USER"
                        )
            yield engine
        finally:
            engine.dispose()
    finally:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            quoted_name = admin_engine.dialect.identifier_preparer.quote(database_name)
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quoted_name} WITH (FORCE)")
        admin_engine.dispose()


class _Queue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, UUID]] = []

    def enqueue(self, task_name: str, *, file_id: UUID, connection: sa.Connection) -> int:
        assert connection.in_transaction()
        self.jobs.append((task_name, file_id))
        return len(self.jobs)

    def enqueue_recompute_po(
        self,
        *,
        workspace_id: UUID,
        po_document_id: UUID,
        connection: sa.Connection,
    ) -> int:
        del workspace_id
        assert connection.in_transaction()
        self.jobs.append(("recompute_po", po_document_id))
        return len(self.jobs)

    def enqueue_dirty_scopes(self, *, workspace_id: UUID, connection: sa.Connection) -> int:
        del workspace_id
        assert connection.in_transaction()
        return len(self.jobs)

    def enqueue_reconciliation(self, *, run_id: UUID, connection: sa.Connection) -> int:
        assert connection.in_transaction()
        self.jobs.append(("evaluate_cases", run_id))
        return len(self.jobs)

    def enqueue_export(self, *, export_id: UUID, connection: sa.Connection) -> int:
        assert connection.in_transaction()
        self.jobs.append(("build_export", export_id))
        return len(self.jobs)

    def enqueue_pending_recomputes(self, *, workspace_id: UUID, connection: sa.Connection) -> int:
        del workspace_id
        assert connection.in_transaction()
        return len(self.jobs)


def _seed_workspace(engine: Engine, label: str) -> tuple[UUID, ActorContext]:
    workspace_id, user_id, config_id = new_id(), new_id(), new_id()
    with engine.begin() as connection:
        connection.execute(
            sa.insert(metadata.tables["workspace"]).values(
                id=workspace_id,
                name=f"Synthetic Phase 4.11/4.13 {label}",
            )
        )
        connection.execute(
            sa.insert(metadata.tables["app_user"]).values(
                id=user_id,
                workspace_id=workspace_id,
                email=f"phase4-{label}-{workspace_id.hex}@example.invalid",
                display_name="Synthetic Test Manager",
                role="manager",
                password_hash="synthetic-unusable-hash",
            )
        )
        connection.execute(
            sa.insert(metadata.tables["config_version"]).values(
                id=config_id,
                workspace_id=workspace_id,
                seq=1,
                body=DEFAULT_CONFIG,
                engine_hash=canonical_hash(DEFAULT_CONFIG["engine"]),
                body_hash=canonical_hash(DEFAULT_CONFIG),
                change_note="Synthetic Phase 4 acceptance baseline",
                created_by=user_id,
            )
        )
        connection.execute(
            sa.insert(metadata.tables["config_active"]).values(
                id=new_id(),
                workspace_id=workspace_id,
                config_version_id=config_id,
                activated_by=user_id,
                version=1,
            )
        )
        connection.execute(
            sa.insert(metadata.tables["reason_code"]),
            [{"id": new_id(), "workspace_id": workspace_id, **row} for row in REASON_CODES],
        )
    return workspace_id, ActorContext(workspace_id, user_id, "manager")


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _invoice_csv_rows(*, invoice_no: str, invoice_date: date, po_no: str) -> bytes:
    return _csv_bytes(
        [
            "Invoice No",
            "Invoice Date",
            "PO Ref",
            "Vendor Name",
            "Vendor GSTIN",
            "Currency",
            "Taxable Total",
            "Grand Total",
            "CGST",
            "SGST",
            "Line No",
            "Description",
            "HSN",
            "UOM",
            "Quantity",
            "Rate",
            "Amount",
        ],
        [
            [
                invoice_no,
                invoice_date.strftime("%d/%m/%Y"),
                po_no,
                _VENDOR_NAME,
                _GSTIN,
                "INR",
                "200",
                "236",
                "18",
                "18",
                "1",
                "Precision Widget PW-100",
                _HSN,
                "NOS",
                "2",
                "100",
                "200",
            ]
        ],
    )


def _po_csv(po_no: str, order_date: date) -> bytes:
    return _csv_bytes(
        [
            "PO No",
            "Order Date",
            "Vendor Name",
            "Vendor GSTIN",
            "Currency",
            "Taxable Total",
            "Grand Total",
            "PO Status",
            "Line No",
            "Description",
            "HSN",
            "UOM",
            "Quantity",
            "Rate",
            "Amount",
        ],
        [
            [
                po_no,
                order_date.strftime("%d/%m/%Y"),
                _VENDOR_NAME,
                _GSTIN,
                "INR",
                "400",
                "472",
                "open",
                "1",
                "Precision Widget PW-100",
                _HSN,
                "NOS",
                "4",
                "100",
                "400",
            ]
        ],
    )


def _grn_csv(grn_no: str, grn_date: date, po_no: str) -> bytes:
    return _csv_bytes(
        [
            "GRN No",
            "GRN Date",
            "PO Ref",
            "Vendor Name",
            "Vendor GSTIN",
            "Currency",
            "Line No",
            "Description",
            "HSN",
            "UOM",
            "Received Qty",
            "Accepted Qty",
            "Rejected Qty",
            "Rate",
            "Amount",
        ],
        [
            [
                grn_no,
                grn_date.strftime("%d/%m/%Y"),
                po_no,
                _VENDOR_NAME,
                _GSTIN,
                "INR",
                "1",
                "Precision Widget PW-100",
                _HSN,
                "NOS",
                "2",
                "2",
                "0",
                "100",
                "200",
            ]
        ],
    )


def _upload_csv(
    ingestion: IngestionService,
    actor: ActorContext,
    batch_id: UUID,
    *,
    zone: UploadZone,
    display_name: str,
    content: bytes,
    job_base: int,
    mapping: Mapping[str, str] | None = None,
) -> UUID:
    receipt = ingestion.receive_file(
        actor,
        batch_id=batch_id,
        zone=zone,
        display_name=display_name,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        file_format=FileFormat.CSV,
        stream=io.BytesIO(content),
    )
    queued = ingestion.queue_files(actor, batch_id=batch_id)
    assert any(item["file_id"] == receipt.file_id for item in queued)
    ingestion.ingest_file(receipt.file_id, job_id=job_base)
    ingestion.parse_file(receipt.file_id, job_id=job_base + 1)
    file_state = ingestion.get_file(actor.workspace_id, receipt.file_id)
    assert file_state is not None
    if file_state["error_code"] == "CSV_MAPPING_REQUIRED":
        assert mapping is not None, f"{display_name} unexpectedly needs a column map"
        ingestion.save_csv_mapping(actor, receipt.file_id, column_mapping=mapping)
        ingestion.ingest_file(receipt.file_id, job_id=job_base + 2)
        ingestion.parse_file(receipt.file_id, job_id=job_base + 3)
        file_state = ingestion.get_file(actor.workspace_id, receipt.file_id)
        assert file_state is not None
    assert file_state["error_code"] is None, (display_name, file_state["error_code"])
    assert int(file_state["docs_found"]) > 0, display_name
    return receipt.file_id


def _create_queued_run(engine: Engine, workspace_id: UUID, actor_id: UUID) -> UUID:
    active = metadata.tables["config_active"]
    config_version = metadata.tables["config_version"]
    run = metadata.tables["recon_run"]
    with engine.begin() as connection:
        config_id, engine_hash = connection.execute(
            sa.select(config_version.c.id, config_version.c.engine_hash)
            .select_from(
                active.join(
                    config_version,
                    active.c.config_version_id == config_version.c.id,
                )
            )
            .where(active.c.workspace_id == workspace_id)
        ).one()
        run_id = new_id()
        scope = {"kind": "all"}
        scope_hash = hashlib.sha256(b'{"kind":"all"}').hexdigest()
        connection.execute(
            sa.insert(run).values(
                id=run_id,
                workspace_id=workspace_id,
                status="queued",
                trigger_kind="user",
                scope=scope,
                scope_hash=scope_hash,
                run_date=date(2026, 10, 31),
                engine_version=ENGINE_VERSION,
                rules_hash=engine_hash,
                config_version_id=config_id,
                ai_mode="rules_only",
                ai_consent=False,
                total_cases=0,
                done_cases=0,
                status_counts={},
                metrics={},
                requested_by=actor_id,
            )
        )
    return run_id


def _worker_test_settings(engine: Engine) -> Settings:
    return Settings(
        database_url=engine.url.render_as_string(hide_password=False),
        s3_endpoint_url="http://127.0.0.1:9000",
        s3_region="us-east-1",
        s3_bucket="reconbench-worker-test",
        s3_api_access_key="synthetic-access-key",
        s3_api_secret_key="synthetic-secret-key",
        public_base_url="http://localhost:5173",
    )


def _start_application_worker(engine: Engine, log_path: Path) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    settings = _worker_test_settings(engine)
    environment.update(
        {
            "RB_DATABASE_URL": settings.database_url,
            "RB_S3_ENDPOINT_URL": settings.s3_endpoint_url,
            "RB_S3_REGION": settings.s3_region,
            "RB_S3_BUCKET": settings.s3_bucket,
            "RB_S3_API_ACCESS_KEY": settings.s3_api_access_key.get_secret_value(),
            "RB_S3_API_SECRET_KEY": settings.s3_api_secret_key.get_secret_value(),
            "RB_PUBLIC_BASE_URL": settings.public_base_url,
            "RB_ENV": "ci",
            "RB_RECONCILIATION_PROCESSES": "1",
        }
    )
    command = (
        "from reconbench_app.jobs import worker_main; "
        "raise SystemExit(worker_main(fetch_job_polling_interval=0.05, "
        "update_heartbeat_interval=0.2, stalled_worker_timeout=2, "
        "shutdown_graceful_timeout=20))"
    )
    with log_path.open("wb") as log_file:
        return subprocess.Popen(
            [sys.executable, "-c", command],
            cwd=ROOT / "backend",
            env=environment,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )


def _wait_for_worker_count(
    engine: Engine,
    worker: subprocess.Popen[bytes],
    log_path: Path,
    *,
    expected: int,
    timeout_seconds: float = 20,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if worker.poll() is not None:
            raise RuntimeError(
                f"application worker exited with {worker.returncode}: "
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        with engine.connect() as connection:
            count = int(connection.scalar(sa.text("SELECT count(*) FROM procrastinate_workers")))
        if count == expected:
            return
        time.sleep(0.05)
    raise TimeoutError(f"expected {expected} registered application workers")


def _wait_for_worker_log(
    worker: subprocess.Popen[bytes],
    log_path: Path,
    message: str,
    *,
    timeout_seconds: float = 20,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if worker.poll() is not None:
            raise RuntimeError(
                f"application worker exited with {worker.returncode}: "
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        if log_path.exists() and message in log_path.read_text(
            encoding="utf-8", errors="replace"
        ):
            return
        time.sleep(0.05)
    raise TimeoutError(f"application worker did not log {message!r}")


def _wait_for_postgres_sleep(
    engine: Engine,
    worker: subprocess.Popen[bytes],
    log_path: Path,
    *,
    expected: bool,
    timeout_seconds: float = 20,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if worker.poll() is not None:
            raise RuntimeError(
                f"application worker exited with {worker.returncode}: "
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        with engine.connect() as connection:
            sleeping = bool(
                connection.scalar(
                    sa.text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event = 'PgSleep')"
                    )
                )
            )
        if sleeping is expected:
            return
        time.sleep(0.05)
    raise TimeoutError(f"application worker sleeping state did not become {expected}")


def _wait_until_no_postgres_sleep(engine: Engine, *, timeout_seconds: float = 20) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            sleeping = bool(
                connection.scalar(
                    sa.text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event = 'PgSleep')"
                    )
                )
            )
        if not sleeping:
            return
        time.sleep(0.05)
    raise TimeoutError("the killed application task kept a PostgreSQL transaction active")


def _wait_until_no_backend_wait(
    engine: Engine,
    wait_event: str,
    *,
    timeout_seconds: float = 20,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = bool(
                connection.scalar(
                    sa.text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event = :wait_event)"
                    ),
                    {"wait_event": wait_event},
                )
            )
        if not waiting:
            return
        time.sleep(0.05)
    raise TimeoutError(f"a backend remained blocked on {wait_event}")


def _ingest_month_bundle(
    engine: Engine,
    actor: ActorContext,
    ingestion: IngestionService,
    batch_id: UUID,
    month: str,
    po_no: str,
    job_base: int,
) -> None:
    month_date = date(2026, 9, 1) if month == "sep" else date(2026, 10, 1)
    _upload_csv(
        ingestion,
        actor,
        batch_id,
        zone=UploadZone.PO,
        display_name=f"{month}-{po_no}.csv",
        content=_po_csv(po_no, month_date),
        job_base=job_base,
    )
    grn_mapping = {"PO Ref": "po_refs", "GRN No": "doc_no", "GRN Date": "doc_date"}
    invoice_mapping = {
        "PO Ref": "po_refs",
        "Invoice No": "doc_no",
        "Invoice Date": "doc_date",
    }
    for shipment in (1, 2):
        day = 5 + shipment * 10
        grn_no = f"GRN-{month.upper()}-{shipment}-{po_no[-3:]}"
        invoice_no = f"INV-{month.upper()}-{shipment}-{po_no[-3:]}"
        grn_date = month_date.replace(day=day - 2)
        invoice_date = month_date.replace(day=day)
        base = job_base + shipment * 10
        _upload_csv(
            ingestion,
            actor,
            batch_id,
            zone=UploadZone.GRN,
            display_name=f"{grn_no}.csv",
            content=_grn_csv(grn_no, grn_date, po_no),
            job_base=base,
            mapping=grn_mapping,
        )
        _upload_csv(
            ingestion,
            actor,
            batch_id,
            zone=UploadZone.INVOICE,
            display_name=f"{invoice_no}.csv",
            content=_invoice_csv_rows(
                invoice_no=invoice_no,
                invoice_date=invoice_date,
                po_no=po_no,
            ),
            job_base=base + 2,
            mapping=invoice_mapping,
        )
    VendorResolutionService(engine).resolve_workspace(actor.workspace_id)
    MappingService(engine).resolve_workspace(actor.workspace_id)
    ledger = LedgerRebuildService(engine)
    with engine.connect() as connection:
        po_ids = list(
            connection.scalars(
                sa.select(metadata.tables["document"].c.id).where(
                    metadata.tables["document"].c.workspace_id == actor.workspace_id,
                    metadata.tables["document"].c.doc_type == "po",
                    metadata.tables["document"].c.doc_no == po_no,
                )
            )
        )
    assert len(po_ids) == 1
    ledger.rebuild_po(actor.workspace_id, UUID(str(po_ids[0])))


def _business_results(
    engine: Engine, workspace_id: UUID, run_id: UUID
) -> dict[str, tuple[Any, ...]]:
    document = metadata.tables["document"]
    case = metadata.tables["recon_case"]
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(
                document.c.doc_no,
                case.c.status,
                case.c.reason_codes,
                case.c.invoice_taxable,
                case.c.invoice_total,
                case.c.expected_taxable,
                case.c.variance,
                case.c.payable_taxable,
                case.c.at_risk,
                case.c.at_risk_gst,
                case.c.blocked_value,
            )
            .select_from(case.join(document, case.c.invoice_id == document.c.id))
            .where(case.c.workspace_id == workspace_id, case.c.run_id == run_id)
            .order_by(document.c.doc_no)
        ).all()
    return {str(row.doc_no): tuple(row[index] for index in range(1, len(row))) for row in rows}


def test_month_ingestion_permutations_and_parallel_cli_pipeline_parity(
    scratch_engine: Engine,
    tmp_path: Path,
) -> None:
    engine = scratch_engine
    outcomes: list[dict[str, tuple[Any, ...]]] = []
    workspaces: list[tuple[UUID, ActorContext, IngestionService, UUID]] = []

    for label, order in (("october-first", ("oct", "sep")), ("september-first", ("sep", "oct"))):
        workspace_id, actor = _seed_workspace(engine, label)
        queue = _Queue()
        ingestion = IngestionService(
            engine,
            LocalObjectStore(tmp_path / label),
            queue,
        )
        batch = ingestion.create_batch(actor, label=f"Synthetic {label} month")["id"]
        for sequence, month in enumerate(order):
            po_no = "PO-2026-SEP" if month == "sep" else "PO-2026-OCT"
            _ingest_month_bundle(
                engine,
                actor,
                ingestion,
                UUID(str(batch)),
                month,
                po_no,
                sequence * 100,
            )

        run_id = _create_queued_run(engine, workspace_id, actor.actor_id)
        snapshot_data = ReconciliationService(engine)._load_snapshot(
            workspace_id,
            run_date=date(2026, 10, 31),
            scope={"kind": "all"},
        )
        snapshot, _manifest, config, _config_id, target_ids, _rows, _lines = snapshot_data
        assert len(target_ids) == 4
        assert len(partition_components(snapshot, config)) == 2

        service = ReconciliationService(engine, process_workers=2)
        cases = service.evaluate_run(run_id)
        assert len(cases) == 4
        outcomes.append(_business_results(engine, workspace_id, run_id))
        workspaces.append((workspace_id, actor, ingestion, run_id))

        frozen_snapshot = tmp_path / f"{label}-snapshot.json"
        cli_output = tmp_path / f"{label}-cli-cases.json"
        frozen_snapshot.write_text(
            json.dumps(
                {
                    "snapshot": snapshot.model_dump(mode="json", exclude_computed_fields=True),
                    "config": config.model_dump(mode="json"),
                    "run_date": "2026-10-31",
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        assert core_cli_main(["run-snapshot", str(frozen_snapshot), "--out", str(cli_output)]) == 0
        expected = (
            json.dumps(
                [item.model_dump(mode="json") for item in cases],
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        assert cli_output.read_bytes() == expected

    assert outcomes[0] == outcomes[1]
    for _workspace_id, actor, _ingestion, _run_id in workspaces:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(metadata.tables["po_line_ledger"])
                    .where(metadata.tables["po_line_ledger"].c.workspace_id == actor.workspace_id)
                )
                == 2
            )


def _insert_5000_benchmark_invoices(
    engine: Engine,
    workspace_id: UUID,
    actor_id: UUID,
    *,
    count: int,
) -> None:
    """Seed parsed-state synthetic rows; parser time is outside the run-time gate."""

    batch_id, source_file_id = new_id(), new_id()
    source_hash = hashlib.sha256(f"benchmark-source:{workspace_id}".encode()).hexdigest()
    with engine.begin() as connection:
        connection.execute(
            sa.insert(metadata.tables["batch"]).values(
                id=batch_id,
                workspace_id=workspace_id,
                label="Synthetic parsed-state 5,000 invoice benchmark",
                created_by=actor_id,
            )
        )
        connection.execute(
            sa.insert(metadata.tables["source_file"]).values(
                id=source_file_id,
                workspace_id=workspace_id,
                batch_id=batch_id,
                sha256=source_hash,
                display_name="synthetic-5000-invoices.csv",
                zone="invoice",
                zone_uploaded="invoice",
                format="csv",
                status="parsed",
                size_bytes=count * 128,
                object_key=f"{workspace_id}/sha256/{source_hash}",
                docs_found=count,
                docs_new=count,
                uploaded_by=actor_id,
            )
        )

        document_rows: list[dict[str, Any]] = []
        version_rows: list[dict[str, Any]] = []
        line_rows: list[dict[str, Any]] = []
        for index in range(count):
            invoice_no = f"SCALE-2026-{index + 1:05d}"
            taxable = Decimal(100 + index)
            tax_each = (taxable * Decimal("0.09")).quantize(Decimal("0.01"))
            total = taxable + tax_each * 2
            document_id, version_id = new_id(), new_id()
            key = hashlib.sha256(f"{workspace_id}:{invoice_no}".encode()).hexdigest()
            document_rows.append(
                {
                    "id": document_id,
                    "workspace_id": workspace_id,
                    "doc_type": "invoice",
                    "natural_key": key,
                    "batch_id": batch_id,
                    "doc_no": invoice_no,
                    "canonical_doc_no": invoice_no,
                    "doc_date": date(2026, 4, 1),
                    "grand_total": total,
                }
            )
            version_rows.append(
                {
                    "id": version_id,
                    "workspace_id": workspace_id,
                    "document_id": document_id,
                    "seq": 1,
                    "content_hash": key,
                    "doc_no": invoice_no,
                    "canonical_doc_no": invoice_no,
                    "doc_date": date(2026, 4, 1),
                    "vendor_name": _VENDOR_NAME,
                    "vendor_name_norm": "synthetic accuracy fixtures pvt ltd",
                    "vendor_gstin": _GSTIN,
                    "vendor_pan": "AAPFU0939F",
                    "vendor_state_code": "27",
                    "currency": "INR",
                    "taxable_total": taxable,
                    "cgst_total": tax_each,
                    "sgst_total": tax_each,
                    "grand_total": total,
                    "extraction_method": "csv_mapping",
                    "extraction_conf": Decimal("0.90"),
                    "source_file_id": source_file_id,
                    "parser_version": "phase4-benchmark",
                    "needs_verification": False,
                }
            )
            line_rows.append(
                {
                    "id": new_id(),
                    "workspace_id": workspace_id,
                    "document_version_id": version_id,
                    "line_no": 1,
                    "line_type": "goods",
                    "description": "Synthetic Benchmark Item",
                    "description_norm": "synthetic benchmark item",
                    "tokens": ["synthetic", "benchmark", "item"],
                    "hsn_sac": _HSN,
                    "uom": "NOS",
                    "uom_as_read": "NOS",
                    "quantity": Decimal("1"),
                    "rate": taxable,
                    "amount": taxable,
                    "tax_rate": Decimal("18"),
                    "cgst_amount": tax_each,
                    "sgst_amount": tax_each,
                    "extras": {},
                }
            )
        connection.execute(sa.insert(metadata.tables["document"]), document_rows)
        connection.execute(sa.insert(metadata.tables["document_version"]), version_rows)
        connection.execute(sa.insert(metadata.tables["doc_line"]), line_rows)
        connection.execute(
            sa.update(metadata.tables["document"])
            .where(metadata.tables["document"].c.workspace_id == workspace_id)
            .values(
                current_version_id=sa.select(metadata.tables["document_version"].c.id)
                .where(
                    metadata.tables["document_version"].c.workspace_id == workspace_id,
                    metadata.tables["document_version"].c.document_id
                    == metadata.tables["document"].c.id,
                )
                .scalar_subquery()
            )
        )


def test_5000_invoice_database_snapshot_and_write_complete_within_60_seconds(
    scratch_engine: Engine,
) -> None:
    engine = scratch_engine
    workspace_id, actor = _seed_workspace(engine, "scale-5000")
    _insert_5000_benchmark_invoices(engine, workspace_id, actor.actor_id, count=5_000)
    run_id = _create_queued_run(engine, workspace_id, actor.actor_id)

    started = time.perf_counter()
    cases = ReconciliationService(engine, process_workers=1).evaluate_run(run_id)
    elapsed = time.perf_counter() - started

    assert len(cases) == 5_000
    assert elapsed <= 60, f"5,000 invoices took {elapsed:.3f}s including snapshot and persistence"
    assert all(item.status.value == "unlinked" for item in cases)
    with engine.connect() as connection:
        run_state = connection.execute(
            sa.select(
                metadata.tables["recon_run"].c.status,
                metadata.tables["recon_run"].c.total_cases,
                metadata.tables["recon_run"].c.done_cases,
            ).where(metadata.tables["recon_run"].c.id == run_id)
        ).one()
        persisted_cases = connection.scalar(
            sa.select(sa.func.count())
            .select_from(metadata.tables["recon_case"])
            .where(
                metadata.tables["recon_case"].c.workspace_id == workspace_id,
                metadata.tables["recon_case"].c.run_id == run_id,
            )
        )
        persisted_lines = connection.scalar(
            sa.select(sa.func.count())
            .select_from(metadata.tables["recon_line_result"])
            .where(
                metadata.tables["recon_line_result"].c.workspace_id == workspace_id,
                metadata.tables["recon_line_result"].c.run_id == run_id,
            )
        )
    assert tuple(run_state) == ("succeeded", 5_000, 5_000)
    assert persisted_cases == 5_000
    assert persisted_lines == 5_000
    print(f"5,000-case application reconciliation: {elapsed:.3f}s")


def test_application_worker_global_drain_and_killed_reconciliation_rollback(
    scratch_engine: Engine,
    tmp_path: Path,
) -> None:
    engine = scratch_engine
    workspace_id, actor = _seed_workspace(engine, "worker-lifecycle")
    queue_stub = _Queue()
    ingestion = IngestionService(engine, LocalObjectStore(tmp_path / "worker"), queue_stub)
    batch_id = UUID(str(ingestion.create_batch(actor, label="worker lifecycle")["id"]))
    _ingest_month_bundle(engine, actor, ingestion, batch_id, "oct", "PO-2026-OCT", 0)

    settings = _worker_test_settings(engine)
    app, queue = register_ingestion_tasks(settings, engine, make_s3_client(settings))
    worker_log = tmp_path / "reconbench-worker.log"
    worker: subprocess.Popen[bytes] | None = None
    replacement_worker: subprocess.Popen[bytes] | None = None
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE FUNCTION phase4_gate_sleep() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN PERFORM pg_sleep(2); RETURN NULL; END $$"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER phase4_gate_sleep AFTER INSERT ON recon_case "
            "FOR EACH STATEMENT EXECUTE FUNCTION phase4_gate_sleep()"
        )

    try:
        worker = _start_application_worker(engine, worker_log)
        _wait_for_worker_count(engine, worker, worker_log, expected=1)

        drain_run_id = _create_queued_run(engine, workspace_id, actor.actor_id)
        with engine.begin() as connection:
            queue.enqueue_reconciliation(run_id=drain_run_id, connection=connection)
        _wait_for_postgres_sleep(engine, worker, worker_log, expected=True)

        drained_count = drain_worker_processes(settings.database_url, timeout_seconds=20)
        assert drained_count == 1
        _wait_for_worker_count(engine, worker, worker_log, expected=0)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.select(metadata.tables["recon_run"].c.status).where(
                        metadata.tables["recon_run"].c.id == drain_run_id
                    )
                )
                == "succeeded"
            )
            assert (
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(metadata.tables["recon_case"])
                    .where(metadata.tables["recon_case"].c.run_id == drain_run_id)
                )
                == 2
            )

        parked_run_id = _create_queued_run(engine, workspace_id, actor.actor_id)
        with engine.begin() as connection:
            queue.enqueue_reconciliation(run_id=parked_run_id, connection=connection)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.select(metadata.tables["recon_run"].c.status).where(
                        metadata.tables["recon_run"].c.id == parked_run_id
                    )
                )
                == "queued"
            )

        resume_worker_processes(settings.database_url)
        _wait_for_worker_count(engine, worker, worker_log, expected=1)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with engine.connect() as connection:
                parked_run_status = connection.scalar(
                    sa.select(metadata.tables["recon_run"].c.status).where(
                        metadata.tables["recon_run"].c.id == parked_run_id
                    )
                )
            if parked_run_status == "succeeded":
                break
            if worker.poll() is not None:
                raise RuntimeError(
                    f"application worker exited with {worker.returncode}: "
                    f"{worker_log.read_text(encoding='utf-8', errors='replace')}"
                )
            time.sleep(0.05)
        else:
            raise TimeoutError("the resumed worker did not process the queued reconciliation")

        # The pause must survive the drain command returning and fence a new
        # process launched by a supervisor. Kill one parked replacement and
        # launch another to model a restart loop while maintenance is active.
        assert drain_worker_processes(settings.database_url, timeout_seconds=20) == 1
        _wait_for_worker_count(engine, worker, worker_log, expected=0)
        worker.kill()
        worker.wait(timeout=10)
        worker = None
        restarted_run_id = _create_queued_run(engine, workspace_id, actor.actor_id)
        with engine.begin() as connection:
            queue.enqueue_reconciliation(run_id=restarted_run_id, connection=connection)

        replacement_log = tmp_path / "reconbench-replacement-worker.log"
        replacement_worker = _start_application_worker(engine, replacement_log)
        _wait_for_worker_log(
            replacement_worker,
            replacement_log,
            "controlled worker parked by persisted maintenance pause",
        )
        _wait_for_worker_count(engine, replacement_worker, replacement_log, expected=0)
        with engine.connect() as connection:
            assert connection.scalar(
                sa.select(metadata.tables["worker_control"].c.paused).where(
                    metadata.tables["worker_control"].c.control_id == 1
                )
            ) is True
            assert connection.scalar(
                sa.select(metadata.tables["recon_run"].c.status).where(
                    metadata.tables["recon_run"].c.id == restarted_run_id
                )
            ) == "queued"

        replacement_worker.kill()
        replacement_worker.wait(timeout=10)
        replacement_worker = None

        replacement_log = tmp_path / "reconbench-restarted-worker.log"
        replacement_worker = _start_application_worker(engine, replacement_log)
        _wait_for_worker_log(
            replacement_worker,
            replacement_log,
            "controlled worker parked by persisted maintenance pause",
        )
        _wait_for_worker_count(engine, replacement_worker, replacement_log, expected=0)
        with engine.connect() as connection:
            assert connection.scalar(
                sa.select(metadata.tables["recon_run"].c.status).where(
                    metadata.tables["recon_run"].c.id == restarted_run_id
                )
            ) == "queued"

        resume_worker_processes(settings.database_url)
        _wait_for_worker_count(
            engine, replacement_worker, replacement_log, expected=1
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with engine.connect() as connection:
                restarted_run_status = connection.scalar(
                    sa.select(metadata.tables["recon_run"].c.status).where(
                        metadata.tables["recon_run"].c.id == restarted_run_id
                    )
                )
            if restarted_run_status == "succeeded":
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("a resumed replacement worker did not process its queued run")

        worker = replacement_worker
        replacement_worker = None

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE OR REPLACE FUNCTION phase4_gate_sleep() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "PERFORM pg_advisory_xact_lock(4276115, 411); RETURN NULL; END $$"
            )

        killed_run_id = _create_queued_run(engine, workspace_id, actor.actor_id)
        blocker = engine.connect()
        blocker.exec_driver_sql("SELECT pg_advisory_lock(4276115, 411)")
        blocker.commit()
        with engine.begin() as connection:
            queue.enqueue_reconciliation(run_id=killed_run_id, connection=connection)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if worker.poll() is not None:
                    raise RuntimeError(
                        f"application worker exited with {worker.returncode}: "
                        f"{worker_log.read_text(encoding='utf-8', errors='replace')}"
                    )
                with engine.connect() as connection:
                    waiting_for_advisory_lock = bool(
                        connection.scalar(
                            sa.text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE datname = current_database() "
                                "AND wait_event = 'advisory')"
                            )
                        )
                    )
                if waiting_for_advisory_lock:
                    break
                time.sleep(0.05)
            else:
                raise TimeoutError("application task did not reach the controlled lock point")

            worker.kill()
            worker.wait(timeout=10)
            blocker.exec_driver_sql("SELECT pg_advisory_unlock(4276115, 411)")
            blocker.commit()
            blocker.close()
            _wait_until_no_backend_wait(engine, "advisory")
            worker = None
        finally:
            if not blocker.closed:
                blocker.exec_driver_sql("SELECT pg_advisory_unlock(4276115, 411)")
                blocker.commit()
                blocker.close()

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(metadata.tables["recon_case"])
                    .where(metadata.tables["recon_case"].c.run_id == killed_run_id)
                )
                == 0
            )
            assert (
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(metadata.tables["run_manifest"])
                    .where(metadata.tables["run_manifest"].c.run_id == killed_run_id)
                )
                == 0
            )
            assert (
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(metadata.tables["case_current"])
                    .where(metadata.tables["case_current"].c.run_id == killed_run_id)
                )
                == 0
            )
    finally:
        for process in (replacement_worker, worker):
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        resume_worker_processes(settings.database_url)
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER IF EXISTS phase4_gate_sleep ON recon_case")
            connection.exec_driver_sql("DROP FUNCTION IF EXISTS phase4_gate_sleep()")
        app.close()
