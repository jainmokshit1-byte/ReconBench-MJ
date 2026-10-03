"""Append-only case decisions, bulk actions, and Manual Recon."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, cast
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from reconbench_app.api.conventions import ApiProblem, etag_for_version, require_if_match
from reconbench_app.api.reconciliation import _actor, _problem, _service
from reconbench_app.audit import AuditAction, write_audit_event
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.repository import VersionConflict
from reconbench_app.idempotency import (
    IdempotentResult,
    execute_idempotent,
    idempotent_json_response,
)
from reconbench_app.json_serialization import DecimalJSONResponse, to_jsonable

router = APIRouter()
ReviewAction = Literal["approve", "hold", "reject", "note", "release"]


class CaseActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ReviewAction
    reason: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=4000)


class BulkCaseAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    expected_version: int = Field(ge=1)
    action: ReviewAction
    reason: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=4000)


class BulkCaseActionsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BulkCaseAction] = Field(min_length=1, max_length=200)


class ManualReconBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: UUID
    po_ids: list[UUID] = Field(min_length=1, max_length=25)
    grn_ids: list[UUID] = Field(default_factory=list, max_length=100)
    run_date: date = Field(default_factory=date.today)


def _insert_action(
    connection: Connection,
    actor: ActorContext,
    *,
    case_id: UUID,
    expected_version: int,
    action_name: ReviewAction,
    reason: str | None,
    note: str | None,
    bulk_id: UUID | None = None,
) -> dict[str, Any]:
    current = metadata.tables["case_current"]
    case = metadata.tables["recon_case"]
    action = metadata.tables["case_action"]
    row = (
        connection.execute(
            sa.select(
                current.c.invoice_id,
                current.c.version,
                current.c.effective_status,
                current.c.computed_status,
                case.c.id.label("case_id"),
                case.c.input_fingerprint,
                case.c.reason_codes,
            )
            .select_from(current.join(case, case.c.id == current.c.case_id))
            .where(current.c.workspace_id == actor.workspace_id, current.c.case_id == case_id)
            .with_for_update(of=current)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return {"case_id": str(case_id), "status": 404, "code": "not_found"}
    version = int(row["version"])
    if version != expected_version:
        raise VersionConflict(
            {
                "case_id": str(case_id),
                "effective_status": row["effective_status"],
                "version": version,
            }
        )
    if action_name == "approve" and not (reason and reason.strip()):
        raise ApiProblem(
            status=422,
            title="Approval reason required",
            detail="Enter a reason before approving this case.",
            code="approval_reason_required",
        )
    if action_name == "note" and not (note and note.strip()):
        raise ApiProblem(
            status=422,
            title="Note required",
            detail="Enter a note before saving it.",
            code="case_note_required",
        )
    if (
        action_name == "approve"
        and row["computed_status"] == "exception"
        and actor.actor_role != "manager"
    ):
        raise ApiProblem(
            status=403,
            title="Manager approval required",
            detail="Only a Manager may approve an Exception.",
            code="manager_approval_required",
        )

    action_id = new_id()
    action_values: dict[str, Any] = {
        "id": action_id,
        "workspace_id": actor.workspace_id,
        "invoice_id": row["invoice_id"],
        "case_id": case_id,
        "action": action_name,
        "reason": reason.strip() if reason else None,
        "note": note.strip() if note else None,
        "actor_id": actor.actor_id,
        "actor_role": actor.actor_role,
        "computed_status": row["computed_status"],
        "reason_codes": list(row["reason_codes"] or []),
        "bulk_id": bulk_id,
    }
    if action_name in {"approve", "hold", "reject"}:
        action_values["input_fingerprint"] = row["input_fingerprint"]
    connection.execute(sa.insert(action).values(**action_values))
    audit_action: AuditAction = cast(AuditAction, f"case.{action_name}")
    write_audit_event(
        connection,
        actor,
        action=audit_action,
        target_type="case",
        target_id=case_id,
        before_state={"effective_status": row["effective_status"], "version": version},
        after_state={"action": action_name, "reason_codes": list(row["reason_codes"] or [])},
        reason=reason,
    )
    new_version = connection.scalar(
        sa.select(current.c.version).where(
            current.c.workspace_id == actor.workspace_id,
            current.c.case_id == case_id,
        )
    )
    return {
        "case_id": str(case_id),
        "action_id": str(action_id),
        "action": action_name,
        "status": 200,
        "effective_status": connection.scalar(
            sa.select(current.c.effective_status).where(
                current.c.workspace_id == actor.workspace_id,
                current.c.case_id == case_id,
            )
        ),
        "version": int(new_version or version + 1),
    }


@router.post("/cases/{case_id}/actions", tags=["Review"])
@requires("cases.decide")
def add_case_action(
    case_id: UUID,
    body: CaseActionBody,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    expected_version = require_if_match(if_match)
    actor = _actor(request)
    try:
        with request.app.state.database_engine.begin() as connection:
            result = _insert_action(
                connection,
                actor,
                case_id=case_id,
                expected_version=expected_version,
                action_name=body.action,
                reason=body.reason,
                note=body.note,
            )
    except ApiProblem as exc:
        raise exc
    if result["status"] == 404:
        return _problem(request, 404, "The reconciliation case was not found.", "not_found")
    return DecimalJSONResponse(
        content=result,
        headers={"ETag": etag_for_version(int(result["version"]))},
    )


@router.post("/cases/bulk-actions", tags=["Review"])
@requires("cases.decide")
def bulk_case_actions(
    body: BulkCaseActionsBody,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    actor = _actor(request)
    if not idempotency_key:
        raise ApiProblem(
            status=400,
            title="Invalid request",
            detail="Idempotency-Key is required for this operation.",
            code="idempotency_key_required",
        )
    bulk_id = new_id()

    def operation(session: Session) -> IdempotentResult:
        items: list[dict[str, Any]] = []
        connection = session.connection()
        for item in body.items:
            try:
                items.append(
                    _insert_action(
                        connection,
                        actor,
                        case_id=item.case_id,
                        expected_version=item.expected_version,
                        action_name=item.action,
                        reason=item.reason,
                        note=item.note,
                        bulk_id=bulk_id,
                    )
                )
            except VersionConflict as exc:
                items.append({"case_id": str(item.case_id), "status": 409, "current": exc.current})
            except ApiProblem as exc:
                items.append({"case_id": str(item.case_id), "status": exc.status, "code": exc.code})
        return IdempotentResult(200, {"bulk_id": bulk_id, "items": items})

    with Session(request.app.state.database_engine) as session:
        result = execute_idempotent(
            session,
            workspace_id=actor.workspace_id,
            user_id=actor.actor_id,
            endpoint="POST /api/v1/cases/bulk-actions",
            key=idempotency_key,
            request_body=body.model_dump(mode="json"),
            operation=operation,
        )
    return idempotent_json_response(result)


@router.post("/manual-recon", tags=["Review"])
@requires("recon.run")
def manual_recon(body: ManualReconBody, request: Request) -> Response:
    try:
        result = _service(request).manual_reconcile(
            _actor(request).workspace_id,
            invoice_id=body.invoice_id,
            po_ids=body.po_ids,
            grn_ids=body.grn_ids,
            run_date=body.run_date,
        )
    except ValueError as exc:
        return _problem(request, 422, str(exc), "invalid_manual_recon")
    return DecimalJSONResponse(content=result.model_dump(mode="json"))


@router.get("/calibration", tags=["Review"])
@requires("data.view")
def get_calibration(request: Request, limit: int = 100) -> Response:
    if not 1 <= limit <= 500:
        return _problem(request, 422, "Limit must be between 1 and 500.", "invalid_limit")
    actor = _actor(request)
    link = metadata.tables["link"].alias("current_link_version")
    prior = metadata.tables["link"].alias("prior_link_version")
    connection = request.app.state.database_engine.connect()
    try:
        links = list(
            connection.execute(
                sa.select(
                    link.c.id.label("decision_link_id"),
                    link.c.from_document_id,
                    link.c.to_document_id,
                    link.c.method.label("method"),
                    link.c.confidence.label("confidence"),
                    link.c.state.label("outcome"),
                    link.c.created_at,
                    prior.c.confidence.label("suggested_confidence"),
                    prior.c.method.label("suggested_method"),
                )
                .select_from(link.outerjoin(prior, prior.c.id == link.c.supersedes_link_id))
                .where(
                    link.c.workspace_id == actor.workspace_id,
                    prior.c.state == "suggested",
                    prior.c.ai_suggested.is_(False),
                    link.c.state.in_(("confirmed", "rejected")),
                )
                .order_by(link.c.created_at.desc(), link.c.id.desc())
                .limit(limit)
            ).mappings()
        )
        actions = list(
            connection.execute(
                sa.select(
                    metadata.tables["case_action"].c.case_id,
                    metadata.tables["case_action"].c.action,
                    metadata.tables["case_action"].c.reason_codes,
                    metadata.tables["case_action"].c.occurred_at,
                )
                .where(
                    metadata.tables["case_action"].c.workspace_id == actor.workspace_id,
                    metadata.tables["case_action"].c.action.in_(("approve", "hold", "reject")),
                )
                .order_by(metadata.tables["case_action"].c.occurred_at.desc())
                .limit(limit)
            ).mappings()
        )
    finally:
        connection.close()
    return DecimalJSONResponse(
        content=to_jsonable(
            {
                "link_decisions": [dict(row) for row in links],
                "overrides": [dict(row) for row in actions],
            }
        )
    )


__all__ = ["router"]
