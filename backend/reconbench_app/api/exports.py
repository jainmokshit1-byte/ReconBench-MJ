"""Authenticated, idempotent endpoints for asynchronous Excel exports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from reconbench_app.api.conventions import ApiProblem, etag_for_version, require_if_match
from reconbench_app.api.reconciliation import _actor, _problem
from reconbench_app.audit import write_audit_event
from reconbench_app.authorization import requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.idempotency import (
    IdempotentResult,
    execute_idempotent,
    idempotent_json_response,
)
from reconbench_app.json_serialization import DecimalJSONResponse, to_jsonable

router = APIRouter()
_XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExportFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID | None = None
    status: list[str] = Field(default_factory=list, max_length=10)
    from_date: date | None = Field(default=None, alias="from")
    to_date: date | None = Field(default=None, alias="to")


class CreateExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    filter: ExportFilter = Field(default_factory=ExportFilter)
    scope: str = Field(default="filtered", pattern=r"^(filtered|all)$")


def _export_actor(request: Request):
    return _actor(request)


def _file_stream(body: Any) -> Iterator[bytes]:
    try:
        while chunk := body.read(64 * 1024):
            yield chunk
    finally:
        body.close()


@router.post("/exports", status_code=202, tags=["Exports"])
@requires("export.create")
def create_export(
    body: CreateExportBody,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    actor = _export_actor(request)
    run = metadata.tables["recon_run"]
    export = metadata.tables["export_file"]
    queue = request.app.state.job_queue
    filter_body = body.filter.model_dump(mode="json", by_alias=True, exclude_none=True)
    if body.scope == "all":
        filter_body = {}
    filter_hash = hashlib.sha256(
        json.dumps(filter_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    def operation(session: Session) -> IdempotentResult:
        run_row = session.execute(
            sa.select(run.c.id, run.c.status).where(
                run.c.workspace_id == actor.workspace_id,
                run.c.id == body.run_id,
            )
        ).one_or_none()
        if run_row is None:
            raise ApiProblem(
                status=404,
                title="Run not found",
                detail="The reconciliation run was not found.",
                code="not_found",
            )
        if run_row.status != "succeeded":
            raise ApiProblem(
                status=409,
                title="Run not complete",
                detail="Export requires a completed reconciliation run.",
                code="run_not_complete",
            )
        if (
            body.filter.from_date
            and body.filter.to_date
            and body.filter.from_date > body.filter.to_date
        ):
            raise ApiProblem(
                status=422,
                title="Invalid export filter",
                detail="The from date must be on or before the to date.",
                code="invalid_date_range",
            )
        export_id = new_id()
        with session.begin_nested():
            session.execute(
                sa.insert(export).values(
                    id=export_id,
                    workspace_id=actor.workspace_id,
                    run_id=body.run_id,
                    included_run_ids=[body.run_id],
                    filter=filter_body,
                    filter_hash=filter_hash,
                    scope=body.scope,
                    as_of=datetime.now(UTC),
                    status="queued",
                    requested_by=actor.actor_id,
                )
            )
            job_id = queue.enqueue_export(export_id=export_id, connection=session.connection())
            session.execute(sa.update(export).where(export.c.id == export_id).values(job_id=job_id))
            write_audit_event(
                session.connection(),
                actor,
                action="export.create",
                target_type="export",
                target_id=export_id,
                after_state={"run_id": str(body.run_id), "filter_hash": filter_hash},
            )
        return IdempotentResult(
            status_code=202,
            body={"id": export_id, "status": "queued"},
            resource_type="exports",
            resource_id=export_id,
        )

    with Session(request.app.state.database_engine) as session:
        result = execute_idempotent(
            session,
            workspace_id=actor.workspace_id,
            user_id=actor.actor_id,
            endpoint="POST /api/v1/exports",
            key=idempotency_key,
            request_body=body.model_dump(mode="json", by_alias=True),
            operation=operation,
        )
    return idempotent_json_response(result)


@router.get("/exports/{export_id}", tags=["Exports"])
@requires("data.view")
def get_export(export_id: UUID, request: Request) -> Response:
    export = metadata.tables["export_file"]
    with request.app.state.database_engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(*export.c).where(
                    export.c.workspace_id == _export_actor(request).workspace_id,
                    export.c.id == export_id,
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return _problem(request, 404, "The export was not found.", "not_found")
    return DecimalJSONResponse(
        content=to_jsonable(dict(row)),
        headers={"ETag": etag_for_version(int(row["version"]))},
    )


@router.post("/exports/{export_id}/retry", status_code=202, tags=["Exports"])
@requires("export.create")
def retry_export(
    export_id: UUID,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    expected_version = require_if_match(if_match)
    export = metadata.tables["export_file"]
    actor = _export_actor(request)
    with request.app.state.database_engine.begin() as connection:
        row = connection.execute(
            sa.select(export.c.status, export.c.version)
            .where(export.c.workspace_id == actor.workspace_id, export.c.id == export_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            return _problem(request, 404, "The export was not found.", "not_found")
        if row.version != expected_version:
            raise ApiProblem(
                status=409,
                title="Export changed",
                detail="Reload the export before retrying it.",
                code="version_conflict",
            )
        if row.status != "failed":
            raise ApiProblem(
                status=409,
                title="Export is not failed",
                detail="Only a failed export can be retried.",
                code="export_not_failed",
            )
        connection.execute(
            sa.update(export)
            .where(export.c.id == export_id)
            .values(
                status="queued",
                error_code=None,
                error_message=None,
                object_key=None,
                version=export.c.version + 1,
            )
        )
        job_id = request.app.state.job_queue.enqueue_export(
            export_id=export_id, connection=connection
        )
        connection.execute(sa.update(export).where(export.c.id == export_id).values(job_id=job_id))
    return DecimalJSONResponse(content={"id": str(export_id), "status": "queued"}, status_code=202)


@router.get("/exports/{export_id}/download", tags=["Exports"])
@requires("data.view")
def download_export(export_id: UUID, request: Request) -> Response:
    actor = _export_actor(request)
    export = metadata.tables["export_file"]
    with request.app.state.database_engine.begin() as connection:
        row = (
            connection.execute(
                sa.select(export.c.status, export.c.object_key, export.c.expires_at)
                .where(export.c.workspace_id == actor.workspace_id, export.c.id == export_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return _problem(request, 404, "The export was not found.", "not_found")
        if (
            row["status"] != "ready"
            or row["expires_at"] is None
            or row["expires_at"] <= datetime.now(UTC)
        ):
            return _problem(request, 409, "The export is not available.", "export_unavailable")
        connection.execute(
            sa.update(export)
            .where(export.c.workspace_id == actor.workspace_id, export.c.id == export_id)
            .values(
                download_count=export.c.download_count + 1, last_downloaded_at=datetime.now(UTC)
            )
        )
        write_audit_event(
            connection,
            actor,
            action="export.download",
            target_type="export",
            target_id=export_id,
        )
    try:
        object_read = request.app.state.file_storage.get(str(row["object_key"]))
    except Exception:
        return _problem(
            request, 503, "The export file is temporarily unavailable.", "object_store_unavailable"
        )
    return StreamingResponse(
        _file_stream(object_read.stream),
        media_type=_XLSX_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="reconbench-{export_id}.xlsx"',
            "Content-Length": str(object_read.size),
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router"]
