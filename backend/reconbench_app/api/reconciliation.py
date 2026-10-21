"""Run, case, and KPI endpoints for the invoice-first reconciliation workflow."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from reconbench_core.version import ENGINE_VERSION
from sqlalchemy.orm import Session

from reconbench_app.ai import h2
from reconbench_app.api.conventions import (
    ApiProblem,
    conditional_json_response,
    etag_for_version,
)
from reconbench_app.api.pagination import KeysetField, paginate_keyset
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.idempotency import (
    IdempotentResult,
    execute_idempotent,
    idempotent_json_response,
)
from reconbench_app.json_serialization import DecimalJSONResponse, to_jsonable
from reconbench_app.reconciliation_service import ReconciliationService

router = APIRouter()


class RunScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(default="all", pattern=r"^(all|vendor)$")
    vendor_id: UUID | None = None


class CreateRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: RunScope = Field(default_factory=RunScope)
    run_date: date = Field(default_factory=date.today)


def _actor(request: Request) -> ActorContext:
    actor = getattr(request.state, "actor_context", None)
    if not isinstance(actor, ActorContext):
        raise ApiProblem(
            status=401,
            title="Authentication required",
            detail="A valid session is required.",
            code="unauthenticated",
        )
    return actor


def _service(request: Request) -> ReconciliationService:
    service = getattr(request.app.state, "reconciliation_service", None)
    if not isinstance(service, ReconciliationService):
        raise RuntimeError("reconciliation service is unavailable")
    return service


def _problem(request: Request, status: int, detail: str, code: str) -> DecimalJSONResponse:
    from reconbench_app.api.conventions import problem_response

    return problem_response(
        status=status,
        title="Reconciliation request failed",
        detail=detail,
        instance=request.url.path,
        code=code,
    )


@router.post("/runs", status_code=202, tags=["Reconciliation"])
@requires("recon.run")
def create_run(
    body: CreateRunBody,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    actor = _actor(request)
    key = idempotency_key
    if key is None:
        raise ApiProblem(
            status=400,
            title="Invalid request",
            detail="Idempotency-Key is required for this operation.",
            code="idempotency_key_required",
        )
    payload = body.model_dump(mode="json")
    engine = request.app.state.database_engine
    run_table = metadata.tables["recon_run"]
    active = metadata.tables["config_active"]
    config_version = metadata.tables["config_version"]
    queue = request.app.state.job_queue

    def operation(session: Session) -> IdempotentResult:
        config_row = session.execute(
            sa.select(config_version.c.id, config_version.c.engine_hash)
            .select_from(
                active.join(
                    config_version,
                    sa.and_(
                        active.c.workspace_id == config_version.c.workspace_id,
                        active.c.config_version_id == config_version.c.id,
                    ),
                )
            )
            .where(active.c.workspace_id == actor.workspace_id)
        ).one_or_none()
        if config_row is None:
            raise ApiProblem(
                status=409,
                title="Configuration unavailable",
                detail="An active reconciliation configuration is required before a run.",
                code="active_config_missing",
            )
        scope = body.scope.model_dump(mode="json", exclude_none=True)
        if body.scope.kind == "vendor":
            if body.scope.vendor_id is None:
                raise ApiProblem(
                    status=422,
                    title="Invalid run scope",
                    detail="A vendor_id is required for a vendor-scoped run.",
                    code="invalid_run_scope",
                )
            vendor = metadata.tables["vendor"]
            exists = session.scalar(
                sa.select(
                    sa.exists().where(
                        vendor.c.workspace_id == actor.workspace_id,
                        vendor.c.id == body.scope.vendor_id,
                    )
                )
            )
            if not exists:
                raise ApiProblem(
                    status=404,
                    title="Vendor not found",
                    detail="The requested vendor is not in this workspace.",
                    code="not_found",
                )
        else:
            scope = {"kind": "all"}

        run_id = new_id()
        insert = run_table.insert().values(
            id=run_id,
            workspace_id=actor.workspace_id,
            status="queued",
            trigger_kind="user",
            scope=scope,
            scope_hash=_stable_hash(scope),
            run_date=body.run_date,
            engine_version=ENGINE_VERSION,
            rules_hash=str(config_row.engine_hash),
            config_version_id=cast(UUID, config_row.id),
            ai_mode="rules_only",
            ai_consent=False,
            total_cases=0,
            done_cases=0,
            status_counts={},
            metrics={},
            requested_by=actor.actor_id,
        )
        session.execute(insert)
        job_id = queue.enqueue_reconciliation(run_id=run_id, connection=session.connection())
        session.execute(sa.update(run_table).where(run_table.c.id == run_id).values(job_id=job_id))
        return IdempotentResult(
            status_code=202,
            body={"id": run_id, "status": "queued", "done_cases": 0, "total_cases": 0},
            resource_type="runs",
            resource_id=run_id,
        )

    with Session(engine) as session:
        result = execute_idempotent(
            session,
            workspace_id=actor.workspace_id,
            user_id=actor.actor_id,
            endpoint="POST /api/v1/runs",
            key=key,
            request_body=payload,
            operation=operation,
        )
    return idempotent_json_response(result)


@router.get("/runs", tags=["Reconciliation"])
@requires("data.view")
def list_runs(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> Response:
    table = metadata.tables["recon_run"]
    with request.app.state.database_engine.connect() as connection:
        rows = list(
            connection.execute(
                sa.select(
                    table.c.id,
                    table.c.status,
                    table.c.trigger_kind,
                    table.c.scope,
                    table.c.run_date,
                    table.c.total_cases,
                    table.c.done_cases,
                    table.c.status_counts,
                    table.c.metrics,
                    table.c.error_code,
                    table.c.error_message,
                    table.c.queued_at,
                    table.c.started_at,
                    table.c.finished_at,
                    table.c.version,
                )
                .where(table.c.workspace_id == _actor(request).workspace_id)
                .order_by(table.c.queued_at.desc(), table.c.id.desc())
                .limit(limit)
            ).mappings()
        )
    return DecimalJSONResponse(content=to_jsonable([dict(row) for row in rows]))


@router.get("/runs/{run_id}", tags=["Reconciliation"])
@requires("data.view")
def get_run(run_id: UUID, request: Request) -> Response:
    table = metadata.tables["recon_run"]
    with request.app.state.database_engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(*table.c).where(
                    table.c.workspace_id == _actor(request).workspace_id,
                    table.c.id == run_id,
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return _problem(request, 404, "The reconciliation run was not found.", "not_found")
    value = dict(row)
    return conditional_json_response(
        request,
        to_jsonable(value),
        etag_for_version(int(row["version"])),
    )


@router.get("/cases", tags=["Reconciliation"])
@requires("data.view")
def list_cases(
    request: Request,
    vendor_id: UUID | None = None,
    status: list[str] | None = Query(default=None),  # noqa: B008 - FastAPI query metadata.
    from_date: date | None = Query(default=None, alias="from"),  # noqa: B008 - FastAPI query metadata.
    to_date: date | None = Query(default=None, alias="to"),  # noqa: B008 - FastAPI query metadata.
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=200),
    count: bool = False,
    run_id: UUID | None = None,
) -> Response:
    if from_date and to_date and from_date > to_date:
        return _problem(
            request, 422, "The from date must be on or before the to date.", "invalid_date_range"
        )
    workspace_id = _actor(request).workspace_id
    current = metadata.tables["case_current"]
    case = metadata.tables["recon_case"]
    document = metadata.tables["document"]
    run = metadata.tables["recon_run"]
    active = metadata.tables["config_active"]
    vendor = metadata.tables["vendor"]
    date_sort = sa.func.coalesce(current.c.invoice_date, date.max).label("sort_invoice_date")
    invoice_sort = sa.func.coalesce(document.c.canonical_doc_no, document.c.doc_no, "").label(
        "sort_invoice_number"
    )
    natural_sort = sa.cast(document.c.natural_key, sa.Text()).label("natural_key_sort")
    vendor_stale = sa.exists(
        sa.select(sa.literal(1)).where(
            vendor.c.workspace_id == workspace_id,
            vendor.c.id == case.c.vendor_id,
            vendor.c.input_seq > case.c.vendor_seq,
        )
    )
    config_stale = run.c.config_version_id != active.c.config_version_id
    scope_entries = sa.func.jsonb_each_text(case.c.po_seqs).table_valued("key", "value")
    po_document = metadata.tables["document"].alias("stale_po")
    po_stale = sa.exists(
        sa.select(sa.literal(1))
        .select_from(
            scope_entries.join(
                po_document, po_document.c.id == sa.cast(scope_entries.c.key, sa.Uuid)
            )
        )
        .where(po_document.c.input_seq > sa.cast(scope_entries.c.value, sa.BigInteger()))
    )
    is_stale = sa.or_(vendor_stale, config_stale, po_stale).label("is_stale")
    statement = (
        sa.select(
            current.c.case_id,
            current.c.run_id,
            current.c.invoice_id,
            current.c.vendor_id,
            current.c.invoice_date,
            current.c.computed_status,
            current.c.overlay,
            current.c.effective_status,
            current.c.severity_rank,
            current.c.human_touched,
            current.c.version,
            case.c.reason_codes,
            case.c.po_nos,
            case.c.grn_nos,
            case.c.invoice_taxable,
            case.c.invoice_total,
            case.c.expected_taxable,
            case.c.variance,
            case.c.at_risk,
            document.c.doc_no.label("invoice_no"),
            document.c.canonical_doc_no,
            is_stale,
            date_sort,
            invoice_sort,
            natural_sort,
        )
        .select_from(
            current.join(case, current.c.case_id == case.c.id)
            .join(document, document.c.id == current.c.invoice_id)
            .join(run, run.c.id == current.c.run_id)
            .outerjoin(active, active.c.workspace_id == current.c.workspace_id)
        )
        .where(current.c.workspace_id == workspace_id)
    )
    if vendor_id is not None:
        statement = statement.where(current.c.vendor_id == vendor_id)
    # A run's own cases: the invoices it evaluated, whatever their dates.
    if run_id is not None:
        statement = statement.where(current.c.run_id == run_id)
    if status:
        allowed = {
            "duplicate",
            "unlinked",
            "needs_review",
            "exception",
            "pending",
            "matched_flags",
            "matched",
            "approved",
            "on_hold",
            "rejected",
        }
        if not set(status).issubset(allowed):
            return _problem(
                request, 422, "One or more status filters are invalid.", "invalid_status"
            )
        statement = statement.where(current.c.effective_status.in_(status))
    # Undated invoices (unreadable scans) stay visible under any period filter.
    if from_date is not None:
        statement = statement.where(
            sa.or_(current.c.invoice_date.is_(None), current.c.invoice_date >= from_date)
        )
    if to_date is not None:
        statement = statement.where(
            sa.or_(current.c.invoice_date.is_(None), current.c.invoice_date <= to_date)
        )
    context = {
        "workspace_id": workspace_id,
        "vendor_id": vendor_id,
        "run_id": run_id,
        "status": sorted(status or []),
        "from": from_date,
        "to": to_date,
        "sort": "severity,date,invoice,natural_key",
    }
    try:
        with Session(request.app.state.database_engine) as session:
            page = paginate_keyset(
                session,
                statement,
                fields=(
                    KeysetField("severity_rank", current.c.severity_rank),
                    KeysetField("sort_invoice_date", date_sort),
                    KeysetField("sort_invoice_number", invoice_sort),
                    KeysetField("natural_key_sort", natural_sort, unique=True),
                ),
                cursor=cursor,
                limit=limit,
                count=count,
                context=context,
            )
    except ValueError as exc:
        return _problem(request, 422, str(exc), "invalid_cursor_or_limit")
    items = []
    for row in page.items:
        item = dict(row)
        item.pop("sort_invoice_date", None)
        item.pop("sort_invoice_number", None)
        item.pop("natural_key_sort", None)
        items.append(to_jsonable(item))
    payload: dict[str, Any] = {"items": items, "next_cursor": page.next_cursor, "limit": page.limit}
    if count:
        payload["total_count"] = page.total_count
    return DecimalJSONResponse(content=payload)


@router.get("/cases/{case_id}", tags=["Reconciliation"])
@requires("data.view")
def get_case(case_id: UUID, request: Request) -> Response:
    workspace_id = _actor(request).workspace_id
    engine = request.app.state.database_engine
    case = metadata.tables["recon_case"]
    current = metadata.tables["case_current"]
    line = metadata.tables["recon_line_result"]
    action = metadata.tables["case_action"]
    document = metadata.tables["document"]
    version = metadata.tables["document_version"]
    source_file = metadata.tables["source_file"]
    evidence = metadata.tables["field_evidence"]
    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(
                    *case.c,
                    current.c.effective_status,
                    current.c.overlay,
                    current.c.version.label("etag_version"),
                    current.c.human_touched,
                    document.c.doc_no.label("invoice_no"),
                    document.c.canonical_doc_no,
                )
                .select_from(
                    case.join(current, current.c.case_id == case.c.id).join(
                        document, document.c.id == case.c.invoice_id
                    )
                )
                .where(case.c.workspace_id == workspace_id, case.c.id == case_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return _problem(request, 404, "The reconciliation case was not found.", "not_found")
        is_stale = bool(
            connection.scalar(
                sa.text(
                    "SELECT is_stale FROM v_case_current "
                    "WHERE workspace_id = :workspace_id AND invoice_id = :invoice_id"
                ),
                {"workspace_id": workspace_id, "invoice_id": row["invoice_id"]},
            )
        )
        line_rows = list(
            connection.execute(
                sa.select(*line.c)
                .where(line.c.workspace_id == workspace_id, line.c.case_id == case_id)
                .order_by(line.c.line_no)
            ).mappings()
        )
        opinion_states = h2.line_states(
            connection,
            workspace_id,
            cast(UUID, row["run_id"]),
            [cast(UUID, item["id"]) for item in line_rows],
        )
        history = list(
            connection.execute(
                sa.select(
                    action.c.id,
                    action.c.action,
                    action.c.reason,
                    action.c.note,
                    action.c.actor_role,
                    action.c.occurred_at,
                    action.c.link_id,
                )
                .where(
                    action.c.workspace_id == workspace_id, action.c.invoice_id == row["invoice_id"]
                )
                .order_by(action.c.occurred_at.desc(), action.c.id.desc())
                .limit(100)
            ).mappings()
        )
        input_versions = (
            row["inputs"].get("document_versions", {}) if isinstance(row["inputs"], Mapping) else {}
        )
        version_ids_by_document = (
            {
                UUID(str(document_id)): UUID(str(version_id))
                for document_id, version_id in input_versions.items()
            }
            if isinstance(input_versions, Mapping)
            else {}
        )
        evidence_document_ids = set(version_ids_by_document)
        evidence_document_ids.add(cast(UUID, row["invoice_id"]))
        evidence_document_ids.update(
            cast(UUID, item["po_document_id"])
            for item in line_rows
            if item["po_document_id"] is not None
        )
        for item in line_rows:
            evidence_document_ids.update(
                cast(UUID, value) for value in (item["grn_document_ids"] or [])
            )
        evidence_version_ids = set(version_ids_by_document.values())
        if row["invoice_version_id"] is not None:
            evidence_version_ids.add(cast(UUID, row["invoice_version_id"]))
        documents = (
            list(
                connection.execute(
                    sa.select(
                        document.c.id.label("document_id"),
                        document.c.doc_type,
                        version.c.doc_no,
                        version.c.canonical_doc_no,
                        version.c.doc_date,
                        version.c.id.label("current_version_id"),
                        version.c.source_file_id,
                        version.c.locator,
                        version.c.page_count,
                        source_file.c.display_name,
                        source_file.c.layout_signature,
                    )
                    .select_from(
                        document.join(version, version.c.document_id == document.c.id).outerjoin(
                            source_file, source_file.c.id == version.c.source_file_id
                        )
                    )
                    .where(
                        document.c.workspace_id == workspace_id,
                        document.c.id.in_(evidence_document_ids),
                        version.c.id.in_(evidence_version_ids),
                    )
                    .order_by(document.c.doc_type, version.c.doc_no)
                ).mappings()
            )
            if evidence_document_ids
            else []
        )
        version_ids = [cast(UUID, item["current_version_id"]) for item in documents]
        field_rows = (
            list(
                connection.execute(
                    sa.select(
                        evidence.c.document_version_id,
                        evidence.c.field_name,
                        evidence.c.value_text,
                        evidence.c.confidence,
                        evidence.c.source,
                        evidence.c.page,
                        evidence.c.bbox,
                        evidence.c.snippet,
                    )
                    .where(
                        evidence.c.workspace_id == workspace_id,
                        evidence.c.document_version_id.in_(version_ids),
                    )
                    .order_by(evidence.c.document_version_id, evidence.c.field_name)
                    .limit(500)
                ).mappings()
            )
            if version_ids
            else []
        )

    fields_by_version: dict[UUID, list[dict[str, Any]]] = {}
    for item in field_rows:
        fields_by_version.setdefault(cast(UUID, item["document_version_id"]), []).append(
            {
                "field": item["field_name"],
                "value": item["value_text"],
                "confidence": item["confidence"],
                "source": item["source"],
                "page": item["page"],
                "bbox": item["bbox"],
                "snippet": item["snippet"],
            }
        )
    evidence_docs = []
    for item in documents:
        value = dict(item)
        value["fields"] = fields_by_version.get(cast(UUID, value["current_version_id"]), [])
        evidence_docs.append(value)
    content = {
        "case": to_jsonable({**dict(row), "is_stale": is_stale}),
        "lines": to_jsonable(
            [
                {**dict(item), "ai_opinion": opinion_states[cast(UUID, item["id"])]}
                for item in line_rows
            ]
        ),
        "documents": to_jsonable(evidence_docs),
        "history": to_jsonable([dict(item) for item in history]),
    }
    etag = etag_for_version(int(row["etag_version"]))
    # The ETag is the case version (also used for If-Match), but an H2 opinion can arrive
    # later without changing it, so a case with an eligible line is never answered with 304.
    if any(
        state["state"] != "not_applicable" or state["code"] == h2.NOT_REQUESTED
        for state in opinion_states.values()
    ):
        return DecimalJSONResponse(content=content, headers={"ETag": etag})
    return conditional_json_response(request, content, etag)


@router.get("/kpis", tags=["Reconciliation"])
@requires("data.view")
def get_kpis(
    request: Request,
    from_date: date = Query(alias="from"),  # noqa: B008 - FastAPI query metadata.
    to_date: date = Query(alias="to"),  # noqa: B008 - FastAPI query metadata.
) -> Response:
    if from_date > to_date:
        return _problem(
            request, 422, "The from date must be on or before the to date.", "invalid_date_range"
        )
    with request.app.state.database_engine.connect() as connection:
        row = (
            connection.execute(
                sa.text("SELECT * FROM kpi_summary(:workspace_id, :from_date, :to_date)"),
                {
                    "workspace_id": _actor(request).workspace_id,
                    "from_date": from_date,
                    "to_date": to_date,
                },
            )
            .mappings()
            .one()
        )
    return DecimalJSONResponse(content=to_jsonable(dict(row)))


# Home 4-step tracker; "complete" follows decision G-12. The period uses the KPI rule:
# an invoice without a readable date belongs to every period (revision 0018).
_WORKFLOW_STATUS_SQL = """
WITH docs AS (
  SELECT count(*) FILTER (WHERE current_version_id IS NOT NULL) AS parsed_documents,
         count(*) FILTER (WHERE vendor_id IS NULL) AS unassigned_documents
    FROM document
   WHERE workspace_id = :workspace_id AND withdrawn_at IS NULL
),
files AS (
  SELECT count(*) AS files_in_progress
    FROM source_file
   WHERE workspace_id = :workspace_id AND removed_at IS NULL
     AND status IN ('queued', 'parsing')
),
suggested AS (
  SELECT count(*) AS suggested_links
    FROM link_current lc
    JOIN link l ON l.id = lc.current_link_id
    JOIN document d ON d.id = lc.from_document_id
   WHERE lc.workspace_id = :workspace_id AND l.state = 'suggested' AND d.withdrawn_at IS NULL
),
period_cases AS (
  SELECT count(*) AS current_cases, count(*) FILTER (WHERE is_stale) AS stale_cases
    FROM v_case_current
   WHERE workspace_id = :workspace_id
     AND (invoice_date IS NULL OR invoice_date BETWEEN :from_date AND :to_date)
),
latest_run AS (
  SELECT r.id
    FROM recon_run r
   WHERE r.workspace_id = :workspace_id AND r.status = 'succeeded'
     AND EXISTS (
       SELECT 1
         FROM recon_case c
         JOIN document_version v ON v.id = c.invoice_version_id
        WHERE c.workspace_id = r.workspace_id AND c.run_id = r.id
          AND (v.doc_date IS NULL OR v.doc_date BETWEEN :from_date AND :to_date))
   ORDER BY r.finished_at DESC NULLS LAST, r.queued_at DESC
   LIMIT 1
),
active_run AS (
  SELECT id FROM recon_run
   WHERE workspace_id = :workspace_id AND status IN ('queued', 'running')
   ORDER BY queued_at DESC
   LIMIT 1
)
SELECT docs.parsed_documents, docs.unassigned_documents, files.files_in_progress,
       suggested.suggested_links, period_cases.current_cases, period_cases.stale_cases,
       (SELECT id FROM latest_run) AS run_id,
       (SELECT id FROM active_run) AS active_run_id,
       EXISTS (SELECT 1 FROM export_file e
                WHERE e.workspace_id = :workspace_id AND e.status = 'ready'
                  AND e.run_id = (SELECT id FROM latest_run)) AS has_export
  FROM docs, files, suggested, period_cases
"""


@router.get("/workflow-status", tags=["Reconciliation"])
@requires("data.view")
def get_workflow_status(
    request: Request,
    from_date: date = Query(alias="from"),  # noqa: B008 - FastAPI query metadata.
    to_date: date = Query(alias="to"),  # noqa: B008 - FastAPI query metadata.
) -> Response:
    if from_date > to_date:
        return _problem(
            request, 422, "The from date must be on or before the to date.", "invalid_date_range"
        )
    with request.app.state.database_engine.connect() as connection:
        row = (
            connection.execute(
                sa.text(_WORKFLOW_STATUS_SQL),
                {
                    "workspace_id": _actor(request).workspace_id,
                    "from_date": from_date,
                    "to_date": to_date,
                },
            )
            .mappings()
            .one()
        )
    ingest_done = row["parsed_documents"] > 0 and row["files_in_progress"] == 0
    map_done = (
        row["parsed_documents"] > 0
        and row["unassigned_documents"] == 0
        and row["suggested_links"] == 0
    )
    reconcile_done = row["run_id"] is not None and row["stale_cases"] == 0
    export_done = reconcile_done and bool(row["has_export"])
    steps = [
        {
            "key": "ingest",
            "complete": ingest_done,
            "parsed_documents": row["parsed_documents"],
            "files_in_progress": row["files_in_progress"],
        },
        {
            "key": "map",
            "complete": map_done,
            "unassigned_documents": row["unassigned_documents"],
            "suggested_links": row["suggested_links"],
        },
        {
            "key": "reconcile",
            "complete": reconcile_done,
            "current_cases": row["current_cases"],
            "stale_cases": row["stale_cases"],
        },
        {"key": "export", "complete": export_done},
    ]
    next_step = next((step["key"] for step in steps if not step["complete"]), None)
    return DecimalJSONResponse(
        content=to_jsonable(
            {
                "from": from_date,
                "to": to_date,
                "steps": steps,
                "next_step": next_step,
                "run_id": row["run_id"],
                "active_run_id": row["active_run_id"],
            }
        ),
        headers={"Cache-Control": "no-store"},
    )


def _stable_hash(value: object) -> str:
    import hashlib
    import json

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["router"]
