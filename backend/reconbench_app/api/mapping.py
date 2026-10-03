"""Vendor and document-link endpoints for the Mapping workflow."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from reconbench_app.api.conventions import (
    ApiProblem,
    etag_for_version,
    problem_response,
    require_if_match,
)
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.db.repository import VersionConflict
from reconbench_app.idempotency import (
    IdempotentResult,
    execute_idempotent,
    idempotent_json_response,
    validate_idempotency_key,
)
from reconbench_app.json_serialization import DecimalJSONResponse, to_jsonable
from reconbench_app.mapping_service import MappingService

router = APIRouter()


class AssignVendorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID


class LinkEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern=r"^(confirm|change|unlink)$")
    to_document_id: UUID | None = None


class VendorMergeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    survivor_vendor_id: UUID


class MergeSuggestionDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(accept|reject)$")
    survivor_vendor_id: UUID | None = None


class VendorAliasBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    alias: str = Field(min_length=1, max_length=300)


class BulkConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID | None = None
    threshold: Decimal = Field(default=Decimal("0.95"), ge=0, le=1)


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


def _service(request: Request) -> MappingService:
    service = getattr(request.app.state, "mapping_service", None)
    if not isinstance(service, MappingService):
        raise RuntimeError("mapping service is unavailable")
    return service


def _error(request: Request, *, status: int, detail: str, code: str) -> DecimalJSONResponse:
    return problem_response(
        status=status,
        title="Mapping request failed",
        detail=detail,
        instance=request.url.path,
        code=code,
    )


@router.get("/vendors", tags=["Mapping"])
@requires("data.view")
def get_vendors(
    request: Request,
    search: str | None = Query(default=None, max_length=200),
    has_issues: bool = False,
) -> Response:
    rows = _service(request).list_vendors(
        _actor(request).workspace_id,
        search=search,
        has_issues=has_issues,
    )
    return DecimalJSONResponse(content=to_jsonable(rows))


@router.get("/vendors/unassigned/documents", tags=["Mapping"])
@requires("data.view")
def get_unassigned_documents(
    request: Request,
    doc_type: str | None = Query(default=None, pattern=r"^(po|grn|invoice)$"),
) -> Response:
    rows = _service(request).list_vendor_documents(
        _actor(request).workspace_id,
        None,
        doc_type=doc_type,
    )
    return DecimalJSONResponse(content=to_jsonable(rows))


@router.get("/vendors/{vendor_id}/documents", tags=["Mapping"])
@requires("data.view")
def get_vendor_documents(
    vendor_id: UUID,
    request: Request,
    doc_type: str | None = Query(default=None, pattern=r"^(po|grn|invoice)$"),
) -> Response:
    vendor = _service(request).get_vendor(_actor(request).workspace_id, vendor_id)
    if vendor is None:
        return _error(request, status=404, detail="The vendor was not found.", code="not_found")
    rows = _service(request).list_vendor_documents(
        _actor(request).workspace_id,
        vendor_id,
        doc_type=doc_type,
    )
    return DecimalJSONResponse(content=to_jsonable(rows))


@router.get("/vendors/{vendor_id}", tags=["Mapping"])
@requires("data.view")
def get_vendor(vendor_id: UUID, request: Request) -> Response:
    row = _service(request).get_vendor(_actor(request).workspace_id, vendor_id)
    if row is None:
        return _error(request, status=404, detail="The vendor was not found.", code="not_found")
    return DecimalJSONResponse(content=to_jsonable(row))


@router.get("/links", tags=["Mapping"])
@requires("data.view")
def get_links(
    request: Request,
    source_document_id: UUID | None = None,
    state: str | None = Query(default=None, pattern=r"^(auto|suggested|confirmed|rejected)$"),
) -> Response:
    rows = _service(request).list_links(
        _actor(request).workspace_id,
        source_document_id=source_document_id,
        state=state,
    )
    return DecimalJSONResponse(content=to_jsonable(rows))


@router.get("/vendor-merge-suggestions", tags=["Mapping"])
@requires("data.view")
def get_vendor_merge_suggestions(request: Request) -> Response:
    rows = _service(request).list_merge_suggestions(_actor(request).workspace_id)
    return DecimalJSONResponse(content=to_jsonable(rows))


@router.post("/vendors/{vendor_id}/merge", tags=["Mapping"])
@requires("links.edit")
def merge_vendor(vendor_id: UUID, body: VendorMergeBody, request: Request) -> Response:
    try:
        result = _service(request).merge_vendors(
            _actor(request), source_vendor_id=vendor_id, survivor_vendor_id=body.survivor_vendor_id
        )
    except ValueError as exc:
        return _error(request, status=422, detail=str(exc), code="invalid_vendor_merge")
    if result is None:
        return _error(request, status=404, detail="A vendor was not found.", code="not_found")
    return DecimalJSONResponse(content=to_jsonable(result))


@router.post("/vendor-merge-suggestions/{suggestion_id}/decision", tags=["Mapping"])
@requires("links.edit")
def decide_vendor_merge(
    suggestion_id: UUID,
    body: MergeSuggestionDecisionBody,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    expected = require_if_match(if_match)
    try:
        result = _service(request).decide_merge_suggestion(
            _actor(request),
            suggestion_id,
            decision=body.decision,
            survivor_vendor_id=body.survivor_vendor_id,
            expected_version=expected,
        )
    except VersionConflict as exc:
        return problem_response(
            status=409,
            title="Resource changed",
            detail="The suggestion changed. Reload it before deciding.",
            instance=request.url.path,
            code="version_conflict",
            extensions={"current": to_jsonable(exc.current)},
        )
    except ValueError as exc:
        return _error(request, status=422, detail=str(exc), code="invalid_merge_decision")
    if result is None:
        return _error(
            request, status=404, detail="The merge suggestion was not found.", code="not_found"
        )
    return DecimalJSONResponse(
        content=to_jsonable(result), headers={"ETag": etag_for_version(int(result["version"]))}
    )


@router.get("/vendor-aliases", tags=["Mapping"])
@requires("data.view")
def get_vendor_aliases(request: Request, vendor_id: UUID | None = None) -> Response:
    rows = _service(request).list_vendor_aliases(_actor(request).workspace_id, vendor_id)
    return DecimalJSONResponse(content=to_jsonable(rows))


@router.post("/vendor-aliases", tags=["Mapping"])
@requires("links.edit")
def post_vendor_alias(body: VendorAliasBody, request: Request) -> Response:
    try:
        result = _service(request).add_vendor_alias(
            _actor(request), vendor_id=body.vendor_id, alias_raw=body.alias
        )
    except ValueError as exc:
        return _error(request, status=422, detail=str(exc), code="invalid_vendor_alias")
    if result is None:
        return _error(request, status=404, detail="The vendor was not found.", code="not_found")
    return DecimalJSONResponse(content=to_jsonable(result))


@router.delete("/vendor-aliases/{alias_id}", tags=["Mapping"])
@requires("links.edit")
def remove_vendor_alias(alias_id: UUID, request: Request) -> Response:
    deleted = _service(request).delete_vendor_alias(_actor(request), alias_id)
    if not deleted:
        return _error(request, status=404, detail="The alias was not found.", code="not_found")
    return DecimalJSONResponse(status_code=200, content={"deleted": True})


@router.post("/links/bulk-confirm", tags=["Mapping"])
@requires("links.edit")
def bulk_confirm_links(
    body: BulkConfirmBody,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    actor = _actor(request)
    key = validate_idempotency_key(idempotency_key)
    engine = getattr(request.app.state, "database_engine", None)
    if engine is None:
        raise RuntimeError("database engine is unavailable")
    service = _service(request)
    request_body = body.model_dump(mode="json")
    with Session(engine) as session:
        result = execute_idempotent(
            session,
            workspace_id=actor.workspace_id,
            user_id=actor.actor_id,
            endpoint="POST /api/v1/links/bulk-confirm",
            key=key,
            request_body=request_body,
            operation=lambda active_session: IdempotentResult(
                200,
                service.bulk_confirm(
                    active_session.connection(),
                    actor,
                    vendor_id=body.vendor_id,
                    threshold=body.threshold,
                ),
            ),
        )
    response = idempotent_json_response(result)
    response.headers["Idempotency-Replayed"] = "true" if result.replayed else "false"
    return response


@router.post("/documents/{document_id}/assign-vendor", tags=["Mapping"])
@requires("links.edit")
def assign_vendor(document_id: UUID, body: AssignVendorBody, request: Request) -> Response:
    actor = _actor(request)
    try:
        result = _service(request).assign_document_vendor(actor, document_id, body.vendor_id)
    except ValueError as exc:
        return _error(request, status=422, detail=str(exc), code="invalid_vendor_assignment")
    if result is None:
        return _error(request, status=404, detail="The document was not found.", code="not_found")
    return DecimalJSONResponse(content=to_jsonable(result))


@router.patch("/links/{link_id}", tags=["Mapping"])
@requires("links.edit")
def patch_link(
    link_id: UUID,
    body: LinkEditBody,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    expected = require_if_match(if_match)
    actor = _actor(request)
    try:
        result = _service(request).edit_link(
            actor,
            link_id,
            action=body.action,
            expected_version=expected,
            to_document_id=body.to_document_id,
        )
    except VersionConflict as exc:
        return problem_response(
            status=409,
            title="Resource changed",
            detail="The link changed after it was loaded. Reload the current version.",
            instance=request.url.path,
            code="version_conflict",
            extensions={"current": to_jsonable(exc.current)},
        )
    except ValueError as exc:
        return _error(request, status=422, detail=str(exc), code="invalid_link_edit")
    if result is None:
        return _error(request, status=404, detail="The link was not found.", code="not_found")
    return DecimalJSONResponse(
        content=to_jsonable(result),
        headers={"ETag": etag_for_version(int(result["version_no"]))},
    )


__all__ = ["router"]
