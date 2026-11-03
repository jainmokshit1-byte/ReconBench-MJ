"""Health, diagnostics, and operational status service for ReconBench."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
import structlog
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import BaseModel, ConfigDict, Field
from reconbench_core.version import ENGINE_VERSION

from reconbench_app.ai.provider_connection import ProviderConnectionService
from reconbench_app.ai.settings_service import AiSettingsService
from reconbench_app.authorization import ActorContext
from reconbench_app.configuration import ConfigurationService
from reconbench_app.db.metadata import metadata
from reconbench_app.settings import Settings

logger = structlog.get_logger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _to_iso(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


class InfrastructureComponent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    status: Literal["healthy", "degraded", "unhealthy"]
    latency_ms: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None


class AiProviderHealth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["gemini", "vllm"]
    enabled: bool
    model: str | None = None
    base_url: str | None = None
    connection_state: str
    reason_code: str | None = None
    checked_at: str | None = None
    connected_at: str | None = None
    capabilities: dict[str, Any] | None = None
    key_configured: bool = False
    key_hint: str | None = None


class AiUsageHealth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    calls_today: int = 0
    tokens_today: int = 0
    calls_this_month: int = 0
    tokens_this_month: int = 0
    usd_today: str | None = None
    uncertain_requests_count: int = 0


class AiPrecisionHealth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    waiting_documents: int = 0
    failed_reading_documents: int = 0
    fallback_to_rules: int = 0
    auto_verified_fields: int = 0
    human_verified_fields: int = 0
    total_ai_fields: int = 0
    accuracy_pct: float | None = None
    recent_errors: list[dict[str, Any]] = Field(default_factory=list)


class AiHealthDashboard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["healthy", "degraded", "unhealthy"]
    mode: str
    mode_label: str
    reader_strategy: str
    primary_reader: str | None = None
    scanned_invoices_reader: str
    scanned_invoices_available: bool
    scanned_invoices_reading: bool
    providers: list[AiProviderHealth] = Field(default_factory=list)
    usage: AiUsageHealth = Field(default_factory=AiUsageHealth)
    precision: AiPrecisionHealth = Field(default_factory=AiPrecisionHealth)


class IngestionHealthDashboard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["healthy", "degraded", "unhealthy"]
    total_files: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_doc_type: dict[str, int] = Field(default_factory=dict)
    digital_count: int = 0
    scanned_count: int = 0
    failed_files_count: int = 0
    recent_failed_files: list[dict[str, Any]] = Field(default_factory=list)


class MappingHealthDashboard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["healthy", "degraded", "unhealthy"]
    total_vendors: int = 0
    unassigned_documents: int = 0
    total_links: int = 0
    confirmed_links: int = 0
    suggested_links: int = 0
    three_way_matched_sets: int = 0


class ReconciliationHealthDashboard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["healthy", "degraded", "unhealthy"]
    engine_version: str
    rules_hash: str
    latest_run_id: str | None = None
    latest_run_status: str | None = None
    latest_run_finished_at: str | None = None
    total_cases: int = 0
    done_cases: int = 0
    matched_pct: float | None = None
    exception_count: int = 0
    pending_count: int = 0
    at_risk_total: str | None = None
    grni_total: str | None = None
    stale_cases_count: int = 0


class HealthIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    severity: Literal["critical", "warning", "info"]
    component: str
    title: str
    description: str
    action_hint: str


class SystemHealthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["healthy", "degraded", "unhealthy"]
    timestamp: datetime
    app_version: str = "0.1.0"
    engine_version: str
    infrastructure: dict[str, InfrastructureComponent]
    ai: AiHealthDashboard
    ingestion: IngestionHealthDashboard
    mapping: MappingHealthDashboard
    reconciliation: ReconciliationHealthDashboard
    issues: list[HealthIssue] = Field(default_factory=list)


class HealthService:
    """Aggregates operational metrics across storage, workers, AI, and pipeline stages."""

    def __init__(
        self,
        engine: sa.Engine,
        s3_client: Any,
        settings: Settings,
        job_queue: Any,
        configuration_service: ConfigurationService,
        ai_settings_service: AiSettingsService,
        ai_provider_connection_service: ProviderConnectionService,
    ) -> None:
        self.engine = engine
        self.s3_client = s3_client
        self.settings = settings
        self.job_queue = job_queue
        self.configuration_service = configuration_service
        self.ai_settings_service = ai_settings_service
        self.ai_provider_connection_service = ai_provider_connection_service

    def get_dashboard(self, workspace_id: UUID, actor: ActorContext) -> SystemHealthResponse:
        """Produce the full system health and diagnostic view for the given workspace."""

        now = datetime.now(UTC)
        issues: list[HealthIssue] = []

        # 1. Database check
        db_start = time.perf_counter()
        db_ok = False
        db_latency_ms: float | None = None
        migrations_in_sync = False
        current_heads: list[str] = []
        expected_heads: list[str] = []
        pool_details: dict[str, Any] = {}

        try:
            with self.engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
                db_latency_ms = round((time.perf_counter() - db_start) * 1000, 2)
                db_ok = True
                current_heads = sorted(
                    list(MigrationContext.configure(conn).get_current_heads() or [])
                )
            scripts = ScriptDirectory.from_config(AlembicConfig(str(BACKEND_ROOT / "alembic.ini")))
            expected_heads = sorted(list(scripts.get_heads()))
            migrations_in_sync = set(current_heads) == set(expected_heads)

            pool = self.engine.pool
            pool_details = {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
            }
        except Exception as exc:
            logger.warning("health_db_check_failed", error=str(exc))
            issues.append(
                HealthIssue(
                    severity="critical",
                    component="database",
                    title="Database connection failed",
                    description=f"PostgreSQL query failed: {type(exc).__name__}",
                    action_hint=(
                        "Check RB_DATABASE_URL and verify PostgreSQL container/service is running."
                    ),
                )
            )

        if db_ok and not migrations_in_sync:
            issues.append(
                HealthIssue(
                    severity="warning",
                    component="database",
                    title="Database migrations out of sync",
                    description=(
                        f"Database head {current_heads} differs from expected {expected_heads}."
                    ),
                    action_hint=(
                        "Run 'make migrate' or 'alembic upgrade head' to apply migrations."
                    ),
                )
            )

        db_status: Literal["healthy", "degraded", "unhealthy"] = (
            "healthy" if (db_ok and migrations_in_sync) else "degraded" if db_ok else "unhealthy"
        )
        infra_db = InfrastructureComponent(
            name="PostgreSQL Database",
            status=db_status,
            latency_ms=db_latency_ms,
            details={
                "migrations_in_sync": migrations_in_sync,
                "current_heads": current_heads,
                "expected_heads": expected_heads,
                "pool": pool_details,
            },
            message="Connected and in sync"
            if (db_ok and migrations_in_sync)
            else ("Migrations pending" if db_ok else "Connection failed"),
        )

        # 2. Object Storage check
        s3_start = time.perf_counter()
        s3_ok = False
        s3_latency_ms: float | None = None
        s3_error_msg: str | None = None

        try:
            self.s3_client.list_buckets()
            s3_latency_ms = round((time.perf_counter() - s3_start) * 1000, 2)
            s3_ok = True
        except Exception as exc:
            s3_error_msg = f"{type(exc).__name__}: {exc}"
            logger.warning("health_s3_check_failed", error=str(exc))
            issues.append(
                HealthIssue(
                    severity="critical",
                    component="storage",
                    title="Object storage unreachable",
                    description=(
                        f"Failed connecting to S3 bucket '{self.settings.s3_bucket}': "
                        f"{type(exc).__name__}"
                    ),
                    action_hint=(
                        "Check RB_S3_ENDPOINT_URL and ensure MinIO or S3 container is healthy."
                    ),
                )
            )

        infra_s3 = InfrastructureComponent(
            name="Object Store (S3/MinIO)",
            status="healthy" if s3_ok else "unhealthy",
            latency_ms=s3_latency_ms,
            details={
                "bucket": self.settings.s3_bucket,
                "endpoint": self.settings.s3_endpoint_url,
                "region": self.settings.s3_region,
            },
            message="Bucket accessible" if s3_ok else (s3_error_msg or "Unreachable"),
        )

        # 3. Background Workers & Procrastinate Queue
        workers_ok = False
        active_workers_count = 0
        last_worker_heartbeat: str | None = None
        job_counts: dict[str, int] = {"todo": 0, "doing": 0, "succeeded": 0, "failed": 0}
        ai_workers: list[dict[str, Any]] = []

        if db_ok:
            try:
                with self.engine.connect() as conn:
                    w_res = conn.execute(
                        sa.text(
                            "SELECT count(*), max(last_heartbeat) FROM procrastinate_workers "
                            "WHERE last_heartbeat >= now() - interval '60 seconds'"
                        )
                    ).fetchone()
                    if w_res:
                        active_workers_count = int(w_res[0] or 0)
                        last_worker_heartbeat = w_res[1].isoformat() if w_res[1] else None
                        workers_ok = active_workers_count > 0

                    j_res = conn.execute(
                        sa.text("SELECT status, count(*) FROM procrastinate_jobs GROUP BY status")
                    ).fetchall()
                    for st, cnt in j_res:
                        job_counts[str(st)] = int(cnt)

                    if "ai_worker_heartbeat" in metadata.tables:
                        ai_hb = metadata.tables["ai_worker_heartbeat"]
                        ai_rows = (
                            conn.execute(
                                sa.select(
                                    ai_hb.c.worker_role, ai_hb.c.instance, ai_hb.c.last_seen_at
                                )
                            )
                            .mappings()
                            .all()
                        )
                        ai_workers = [
                            {
                                "worker_role": row["worker_role"],
                                "instance": row["instance"],
                                "last_seen_at": (
                                    row["last_seen_at"].isoformat() if row["last_seen_at"] else None
                                ),
                            }
                            for row in ai_rows
                        ]
            except Exception as exc:
                logger.warning("health_workers_check_failed", error=str(exc))

        if not workers_ok:
            issues.append(
                HealthIssue(
                    severity="warning",
                    component="workers",
                    title="No active background workers",
                    description="No background worker heartbeats recorded in the past 60 seconds.",
                    action_hint=(
                        "Ensure the worker process or container is running "
                        "('docker compose up -d worker')."
                    ),
                )
            )

        if job_counts.get("failed", 0) > 0:
            issues.append(
                HealthIssue(
                    severity="warning",
                    component="workers",
                    title="Failed background jobs detected",
                    description=(f"{job_counts['failed']} background jobs failed in queue."),
                    action_hint=("Review worker logs or retry failed files on Ingestion / Health."),
                )
            )

        infra_workers = InfrastructureComponent(
            name="Background Worker Queue",
            status="healthy" if workers_ok else "degraded",
            details={
                "active_workers": active_workers_count,
                "last_heartbeat": last_worker_heartbeat,
                "job_counts": job_counts,
                "ai_workers": ai_workers,
            },
            message=f"{active_workers_count} active worker(s)"
            if workers_ok
            else "No active workers",
        )

        infrastructure = {
            "database": infra_db,
            "storage": infra_s3,
            "workers": infra_workers,
        }

        # 4. AI & LLM Components
        ai_status: Literal["healthy", "degraded", "unhealthy"] = "healthy"
        ai_view_data: dict[str, Any] = {}
        ai_providers_list: list[AiProviderHealth] = []
        ai_usage_data = AiUsageHealth()
        ai_precision_data = AiPrecisionHealth()

        try:
            ai_view = self.ai_settings_service.view(actor)
            ai_view_data = ai_view

            providers_raw = ai_view.get("providers") or []
            for p in providers_raw:
                conn_info = p.get("connection") or {}
                key_info = p.get("key") or {}
                card_obj = AiProviderHealth(
                    provider=p["provider"],
                    enabled=bool(p.get("enabled")),
                    model=p.get("model"),
                    base_url=p.get("base_url"),
                    connection_state=str(conn_info.get("state") or "not_connected"),
                    reason_code=conn_info.get("reason_code"),
                    checked_at=_to_iso(conn_info.get("checked_at")),
                    connected_at=_to_iso(conn_info.get("connected_at")),
                    capabilities=p.get("capabilities"),
                    key_configured=bool(key_info.get("set")),
                    key_hint=key_info.get("hint"),
                )
                ai_providers_list.append(card_obj)
                if card_obj.connection_state == "failed":
                    issues.append(
                        HealthIssue(
                            severity="warning",
                            component="ai",
                            title=f"AI Provider '{card_obj.provider}' connection failed",
                            description=(f"Last check failed ({card_obj.reason_code or 'error'})."),
                            action_hint=(
                                "Verify API key, endpoint URL, or network access on Settings."
                            ),
                        )
                    )

            # AI Usage
            usage_view = self.ai_settings_service.usage(actor)
            ai_usage_data = AiUsageHealth(
                calls_today=int(usage_view.get("calls_today") or 0),
                tokens_today=int(usage_view.get("tokens_today") or 0),
                calls_this_month=int(usage_view.get("calls_this_month") or 0),
                tokens_this_month=int(usage_view.get("tokens_this_month") or 0),
                usd_today=str(usage_view.get("usd_today"))
                if usage_view.get("usd_today") is not None
                else None,
                uncertain_requests_count=len(usage_view.get("uncertain_requests") or []),
            )
        except Exception as exc:
            logger.warning("health_ai_settings_check_failed", error=str(exc))
            ai_status = "degraded"

        # AI Precision & Request Analytics
        if db_ok:
            try:
                with self.engine.connect() as conn:
                    req_tbl = metadata.tables["ai_request"]
                    req_counts = conn.execute(
                        sa.select(
                            req_tbl.c.request_state,
                            sa.func.count(req_tbl.c.id),
                        )
                        .where(req_tbl.c.workspace_id == workspace_id)
                        .group_by(req_tbl.c.request_state)
                    ).fetchall()

                    total_reqs = 0
                    success_reqs = 0
                    failed_reqs = 0
                    for state, count in req_counts:
                        total_reqs += int(count)
                        if state == "finished":
                            success_reqs += int(count)
                        elif state == "failed":
                            failed_reqs += int(count)

                    # Recent AI errors
                    recent_err_rows = (
                        conn.execute(
                            sa.select(
                                req_tbl.c.id,
                                req_tbl.c.provider,
                                req_tbl.c.error_code,
                                req_tbl.c.skip_code,
                                req_tbl.c.finished_at,
                            )
                            .where(
                                req_tbl.c.workspace_id == workspace_id,
                                (
                                    req_tbl.c.error_code.is_not(None)
                                    | (req_tbl.c.request_state == "failed")
                                ),
                            )
                            .order_by(req_tbl.c.finished_at.desc().nullslast())
                            .limit(5)
                        )
                        .mappings()
                        .all()
                    )

                    recent_errors = [
                        {
                            "id": str(r["id"]),
                            "provider": r["provider"],
                            "error_code": r["error_code"],
                            "skip_code": r["skip_code"],
                            "finished_at": r["finished_at"].isoformat()
                            if r["finished_at"]
                            else None,
                        }
                        for r in recent_err_rows
                    ]

                    # Document reading states
                    doc_tbl = metadata.tables["document"]
                    waiting_docs = (
                        conn.scalar(
                            sa.select(sa.func.count(doc_tbl.c.id)).where(
                                doc_tbl.c.workspace_id == workspace_id,
                                doc_tbl.c.reading_state == "waiting",
                                doc_tbl.c.withdrawn_at.is_(None),
                            )
                        )
                        or 0
                    )
                    failed_docs = (
                        conn.scalar(
                            sa.select(sa.func.count(doc_tbl.c.id)).where(
                                doc_tbl.c.workspace_id == workspace_id,
                                doc_tbl.c.reading_state == "failed",
                                doc_tbl.c.withdrawn_at.is_(None),
                            )
                        )
                        or 0
                    )

                    if failed_docs > 0:
                        issues.append(
                            HealthIssue(
                                severity="warning",
                                component="ai",
                                title=f"{failed_docs} document(s) failed AI reading",
                                description=(
                                    "Documents entered 'failed' reading state during parsing."
                                ),
                                action_hint=(
                                    "Check recent AI errors or inspect document parsing logs."
                                ),
                            )
                        )

                    # Field evidence precision
                    fe_tbl = metadata.tables["field_evidence"]
                    fe_counts = conn.execute(
                        sa.select(
                            fe_tbl.c.origin,
                            fe_tbl.c.verification,
                            sa.func.count(fe_tbl.c.id),
                        )
                        .where(fe_tbl.c.workspace_id == workspace_id)
                        .group_by(fe_tbl.c.origin, fe_tbl.c.verification)
                    ).fetchall()

                    total_ai_fields = 0
                    auto_ver = 0
                    human_ver = 0
                    for origin, ver, cnt in fe_counts:
                        if origin == "ai":
                            total_ai_fields += int(cnt)
                            if ver in ("system_check", "auto_verified"):
                                auto_ver += int(cnt)
                            elif ver in ("user", "human_verified", "human_corrected"):
                                human_ver += int(cnt)

                    acc_pct = None
                    if total_ai_fields > 0:
                        acc_pct = round(((auto_ver + human_ver) / total_ai_fields) * 100, 1)

                    ai_precision_data = AiPrecisionHealth(
                        total_requests=total_reqs,
                        successful_requests=success_reqs,
                        failed_requests=failed_reqs,
                        waiting_documents=int(waiting_docs),
                        failed_reading_documents=int(failed_docs),
                        fallback_to_rules=int(failed_docs),
                        auto_verified_fields=auto_ver,
                        human_verified_fields=human_ver,
                        total_ai_fields=total_ai_fields,
                        accuracy_pct=acc_pct,
                        recent_errors=recent_errors,
                    )
            except Exception as exc:
                logger.warning("health_ai_precision_check_failed", error=str(exc))

        scanned_inv = ai_view_data.get("scanned_invoices") or {}
        ai_dashboard = AiHealthDashboard(
            status=ai_status,
            mode=str(ai_view_data.get("mode") or "rules_only"),
            mode_label=str(ai_view_data.get("mode_label") or "Rules only"),
            reader_strategy=str(ai_view_data.get("reader_strategy") or "off"),
            primary_reader=ai_view_data.get("primary_reader"),
            scanned_invoices_reader=str(scanned_inv.get("reader") or "rules"),
            scanned_invoices_available=bool(scanned_inv.get("ai_available")),
            scanned_invoices_reading=bool(scanned_inv.get("ai_reading")),
            providers=ai_providers_list,
            usage=ai_usage_data,
            precision=ai_precision_data,
        )

        # 5. Invoices & Ingestion Health
        ingestion_status: Literal["healthy", "degraded", "unhealthy"] = "healthy"
        total_files = 0
        file_by_status: dict[str, int] = {}
        doc_by_type: dict[str, int] = {}
        digital_count = 0
        scanned_count = 0
        failed_files_count = 0
        recent_failed_files: list[dict[str, Any]] = []

        if db_ok:
            try:
                with self.engine.connect() as conn:
                    sf_tbl = metadata.tables["source_file"]
                    f_rows = conn.execute(
                        sa.select(sf_tbl.c.status, sa.func.count(sf_tbl.c.id))
                        .where(sf_tbl.c.workspace_id == workspace_id, sf_tbl.c.removed_at.is_(None))
                        .group_by(sf_tbl.c.status)
                    ).fetchall()
                    for st, count in f_rows:
                        cnt = int(count)
                        total_files += cnt
                        file_by_status[str(st)] = cnt
                        if str(st) == "failed":
                            failed_files_count += cnt

                    doc_tbl = metadata.tables["document"]
                    dt_rows = conn.execute(
                        sa.select(doc_tbl.c.doc_type, sa.func.count(doc_tbl.c.id))
                        .where(
                            doc_tbl.c.workspace_id == workspace_id, doc_tbl.c.withdrawn_at.is_(None)
                        )
                        .group_by(doc_tbl.c.doc_type)
                    ).fetchall()
                    for dt, count in dt_rows:
                        doc_by_type[str(dt)] = int(count)

                    dv_tbl = metadata.tables["document_version"]
                    rm_rows = conn.execute(
                        sa.select(dv_tbl.c.reading_method, sa.func.count(dv_tbl.c.id))
                        .where(dv_tbl.c.workspace_id == workspace_id)
                        .group_by(dv_tbl.c.reading_method)
                    ).fetchall()
                    for rm, count in rm_rows:
                        cnt = int(count)
                        if rm in ("ai_vision", "ocr"):
                            scanned_count += cnt
                        else:
                            digital_count += cnt

                    if failed_files_count > 0:
                        ingestion_status = "degraded"
                        rf_rows = (
                            conn.execute(
                                sa.select(
                                    sf_tbl.c.id,
                                    sf_tbl.c.display_name,
                                    sf_tbl.c.error_code,
                                    sf_tbl.c.parse_finished_at,
                                )
                                .where(
                                    sf_tbl.c.workspace_id == workspace_id,
                                    sf_tbl.c.status == "failed",
                                    sf_tbl.c.removed_at.is_(None),
                                )
                                .order_by(sf_tbl.c.parse_finished_at.desc().nullslast())
                                .limit(5)
                            )
                            .mappings()
                            .all()
                        )
                        recent_failed_files = [
                            {
                                "id": str(r["id"]),
                                "display_name": r["display_name"],
                                "error_code": r["error_code"],
                                "parse_finished_at": (
                                    r["parse_finished_at"].isoformat()
                                    if r["parse_finished_at"]
                                    else None
                                ),
                            }
                            for r in rf_rows
                        ]
                        issues.append(
                            HealthIssue(
                                severity="warning",
                                component="ingestion",
                                title=f"{failed_files_count} file(s) failed ingestion",
                                description=(
                                    "One or more files encountered errors during parsing."
                                ),
                                action_hint=(
                                    "Check the failed files below or open Admin Errors to retry."
                                ),
                            )
                        )
            except Exception as exc:
                logger.warning("health_ingestion_check_failed", error=str(exc))
                ingestion_status = "degraded"

        ingestion_dashboard = IngestionHealthDashboard(
            status=ingestion_status,
            total_files=total_files,
            by_status=file_by_status,
            by_doc_type=doc_by_type,
            digital_count=digital_count,
            scanned_count=scanned_count,
            failed_files_count=failed_files_count,
            recent_failed_files=recent_failed_files,
        )

        # 6. Mapping Health
        mapping_status: Literal["healthy", "degraded", "unhealthy"] = "healthy"
        total_vendors = 0
        unassigned_docs = 0
        total_links = 0
        confirmed_links = 0
        suggested_links = 0
        three_way_matched_sets = 0

        if db_ok:
            try:
                with self.engine.connect() as conn:
                    v_tbl = metadata.tables["vendor"]
                    total_vendors = int(
                        conn.scalar(
                            sa.select(sa.func.count(v_tbl.c.id)).where(
                                v_tbl.c.workspace_id == workspace_id
                            )
                        )
                        or 0
                    )

                    doc_tbl = metadata.tables["document"]
                    unassigned_docs = int(
                        conn.scalar(
                            sa.select(sa.func.count(doc_tbl.c.id)).where(
                                doc_tbl.c.workspace_id == workspace_id,
                                doc_tbl.c.vendor_id.is_(None),
                                doc_tbl.c.withdrawn_at.is_(None),
                            )
                        )
                        or 0
                    )

                    lc_tbl = metadata.tables["link_current"]
                    l_tbl = metadata.tables["link"]
                    total_links = int(
                        conn.scalar(
                            sa.select(sa.func.count(lc_tbl.c.id)).where(
                                lc_tbl.c.workspace_id == workspace_id
                            )
                        )
                        or 0
                    )
                    confirmed_links = int(
                        conn.scalar(
                            sa.select(sa.func.count(lc_tbl.c.id))
                            .select_from(lc_tbl.join(l_tbl, l_tbl.c.id == lc_tbl.c.current_link_id))
                            .where(
                                lc_tbl.c.workspace_id == workspace_id,
                                l_tbl.c.state == "confirmed",
                            )
                        )
                        or 0
                    )
                    suggested_links = int(
                        conn.scalar(
                            sa.select(sa.func.count(lc_tbl.c.id))
                            .select_from(lc_tbl.join(l_tbl, l_tbl.c.id == lc_tbl.c.current_link_id))
                            .where(
                                lc_tbl.c.workspace_id == workspace_id,
                                l_tbl.c.state == "suggested",
                            )
                        )
                        or 0
                    )
                    three_way_matched_sets = int(
                        conn.execute(
                            sa.text(
                                "SELECT count(*) FROM v_case_current "
                                "WHERE workspace_id = :workspace_id "
                                "AND effective_status = 'matched'"
                            ),
                            {"workspace_id": workspace_id},
                        ).scalar()
                        or 0
                    )

                    if unassigned_docs > 0:
                        issues.append(
                            HealthIssue(
                                severity="info",
                                component="mapping",
                                title=f"{unassigned_docs} document(s) have unassigned vendors",
                                description=(
                                    "Documents cannot be reconciled until vendor is assigned."
                                ),
                                action_hint=(
                                    "Go to Mapping page to assign vendors or confirm suggestions."
                                ),
                            )
                        )
            except Exception as exc:
                logger.warning("health_mapping_check_failed", error=str(exc))
                mapping_status = "degraded"

        mapping_dashboard = MappingHealthDashboard(
            status=mapping_status,
            total_vendors=total_vendors,
            unassigned_documents=unassigned_docs,
            total_links=total_links,
            confirmed_links=confirmed_links,
            suggested_links=suggested_links,
            three_way_matched_sets=three_way_matched_sets,
        )

        # 7. Reconciliation Health
        recon_status: Literal["healthy", "degraded", "unhealthy"] = "healthy"
        rules_hash_val = "unknown"
        try:
            cfg_res, _ = self.configuration_service.get(workspace_id)
            rules_hash_val = cfg_res.rules_hash
        except Exception:
            pass

        latest_run_id: str | None = None
        latest_run_status: str | None = None
        latest_run_fin: str | None = None
        total_cases = 0
        done_cases = 0
        matched_pct: float | None = None
        exception_count = 0
        pending_count = 0
        at_risk_total: str | None = None
        grni_total: str | None = None
        stale_cases_count = 0

        if db_ok:
            try:
                with self.engine.connect() as conn:
                    r_tbl = metadata.tables["recon_run"]
                    latest_run = (
                        conn.execute(
                            sa.select(
                                r_tbl.c.id,
                                r_tbl.c.status,
                                r_tbl.c.started_at,
                                r_tbl.c.finished_at,
                                r_tbl.c.total_cases,
                                r_tbl.c.done_cases,
                                r_tbl.c.status_counts,
                            )
                            .where(r_tbl.c.workspace_id == workspace_id)
                            .order_by(r_tbl.c.started_at.desc())
                            .limit(1)
                        )
                        .mappings()
                        .first()
                    )

                    if latest_run:
                        latest_run_id = str(latest_run["id"])
                        latest_run_status = latest_run["status"]
                        latest_run_fin = (
                            latest_run["finished_at"].isoformat()
                            if latest_run["finished_at"]
                            else None
                        )
                        total_cases = int(latest_run["total_cases"] or 0)
                        done_cases = int(latest_run["done_cases"] or 0)
                        st_counts = latest_run["status_counts"] or {}
                        matched_c = int(st_counts.get("matched", 0))
                        exception_count = int(st_counts.get("exception", 0))
                        pending_count = int(st_counts.get("pending", 0))
                        if total_cases > 0:
                            matched_pct = round((matched_c / total_cases) * 100, 1)

                    # Live metrics from v_case_current view
                    case_stats = (
                        conn.execute(
                            sa.text(
                                "SELECT "
                                "  count(*) as total, "
                                "  count(*) filter ("
                                "    where effective_status in ('matched', 'resolved')"
                                "  ) as done, "
                                "  count(*) filter (where effective_status = 'matched') as matched, "
                                "  count(*) filter ("
                                "    where effective_status in ('exception', 'needs_review')"
                                "  ) as exceptions, "
                                "  count(*) filter (where effective_status = 'unlinked') as pending, "
                                "  sum(at_risk) as at_risk, "
                                "  count(*) filter (where is_stale) as stale_count "
                                "FROM v_case_current "
                                "WHERE workspace_id = :workspace_id"
                            ),
                            {"workspace_id": workspace_id},
                        )
                        .mappings()
                        .first()
                    )

                    if case_stats and case_stats["total"]:
                        total_cases = int(case_stats["total"] or 0)
                        done_cases = int(case_stats["done"] or 0)
                        exception_count = int(case_stats["exceptions"] or 0)
                        pending_count = int(case_stats["pending"] or 0)
                        matched_c = int(case_stats["matched"] or 0)
                        if total_cases > 0:
                            matched_pct = round((matched_c / total_cases) * 100, 1)
                        if case_stats["at_risk"] is not None:
                            at_risk_total = str(case_stats["at_risk"])
                        stale_cases_count = int(case_stats["stale_count"] or 0)

                    if stale_cases_count > 0:
                        issues.append(
                            HealthIssue(
                                severity="info",
                                component="reconciliation",
                                title=f"{stale_cases_count} stale reconciliation case(s)",
                                description=(
                                    "Cases are stale because upstream documents or rules changed."
                                ),
                                action_hint=(
                                    "Trigger a new reconciliation run on the Reconciliation page."
                                ),
                            )
                        )
            except Exception as exc:
                logger.warning("health_recon_check_failed", error=str(exc))
                recon_status = "degraded"

        reconciliation_dashboard = ReconciliationHealthDashboard(
            status=recon_status,
            engine_version=ENGINE_VERSION,
            rules_hash=rules_hash_val,
            latest_run_id=latest_run_id,
            latest_run_status=latest_run_status,
            latest_run_finished_at=latest_run_fin,
            total_cases=total_cases,
            done_cases=done_cases,
            matched_pct=matched_pct,
            exception_count=exception_count,
            pending_count=pending_count,
            at_risk_total=at_risk_total,
            grni_total=grni_total,
            stale_cases_count=stale_cases_count,
        )

        # Overall Status
        has_critical = any(issue.severity == "critical" for issue in issues)
        has_warning = any(issue.severity == "warning" for issue in issues)

        overall_status: Literal["healthy", "degraded", "unhealthy"]
        if not db_ok or not s3_ok or has_critical:
            overall_status = "unhealthy"
        elif has_warning or not workers_ok:
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        return SystemHealthResponse(
            status=overall_status,
            timestamp=now,
            app_version="0.1.0",
            engine_version=ENGINE_VERSION,
            infrastructure=infrastructure,
            ai=ai_dashboard,
            ingestion=ingestion_dashboard,
            mapping=mapping_dashboard,
            reconciliation=reconciliation_dashboard,
            issues=issues,
        )


__all__ = [
    "AiHealthDashboard",
    "AiPrecisionHealth",
    "AiProviderHealth",
    "AiUsageHealth",
    "HealthIssue",
    "HealthService",
    "InfrastructureComponent",
    "IngestionHealthDashboard",
    "MappingHealthDashboard",
    "ReconciliationHealthDashboard",
    "SystemHealthResponse",
]
