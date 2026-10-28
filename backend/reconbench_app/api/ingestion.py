"""Authenticated upload, batch, document, and processing-error endpoints."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, model_validator
from reconbench_core.schema import UploadZone
from starlette.responses import StreamingResponse

from reconbench_app.api.conventions import (
    ApiProblem,
    ProblemDetails,
    conditional_json_response,
    etag_for_version,
    require_if_match,
)
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.ingestion import (
    AiSuggestionRef,
    BatchVersionConflict,
    IngestionFailure,
    IngestionService,
    ObjectStoreUnavailable,
    safe_display_name,
    sniff_format,
)
from reconbench_app.json_serialization import DecimalJSONResponse, to_jsonable
from reconbench_app.storage import ObjectRead

router = APIRouter()


class BatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=200)
    period_start: date | None = None
    period_end: date | None = None

    @model_validator(mode="after")
    def validate_period(self) -> BatchBody:
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class MismatchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(accept_suggested|keep_zone)$")


class PageSplitDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(accept_split|keep_one)$")


class DuplicateDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(discard|replace|keep)$")


class ColumnMappingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_mapping: dict[str, str] = Field(min_length=3, max_length=128)


class DocumentLineCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    line_no: int = Field(ge=1, le=10_000)
    line_type: Literal["goods", "charge"] | None = None
    description: str | None = Field(default=None, max_length=2_000)
    item_code: str | None = Field(default=None, max_length=256)
    hsn_sac: str | None = Field(default=None, max_length=64)
    uom: str | None = Field(default=None, max_length=64)
    quantity: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    received_qty: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    rejected_qty: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    accepted_qty: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    rate: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    discount: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    amount: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    tax_rate: Decimal | None = Field(default=None, max_digits=5, decimal_places=2)
    cgst_amount: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    sgst_amount: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    igst_amount: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    po_ref: str | None = Field(default=None, max_length=256)
    po_line_ref: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_new_line(self) -> DocumentLineCorrection:
        if self.id is None and not any(
            value not in (None, "")
            for value in (
                self.line_type,
                self.description,
                self.item_code,
                self.hsn_sac,
                self.quantity,
                self.received_qty,
                self.rejected_qty,
                self.accepted_qty,
                self.rate,
                self.discount,
                self.amount,
                self.tax_rate,
                self.cgst_amount,
                self.sgst_amount,
                self.igst_amount,
                self.po_ref,
                self.po_line_ref,
            )
        ):
            raise ValueError("A new line needs at least one value.")
        return self


class AiSuggestionRefBody(BaseModel):
    """Marks a corrected value as taken from an H1 suggestion (it must still match)."""

    model_config = ConfigDict(extra="forbid")

    ai_call_id: UUID
    scope: Literal["header", "line"]
    line_no: int | None = Field(default=None, ge=1, le=10_000)
    field: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_scope(self) -> AiSuggestionRefBody:
        if (self.scope == "header") != (self.line_no is None):
            raise ValueError("Header references have no line_no; line references need one.")
        return self


class InvoiceLineCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    line_no: int = Field(ge=1, le=10_000)
    description: str | None = Field(default=None, max_length=2_000)
    item_code: str | None = Field(default=None, max_length=256)
    hsn_sac: str | None = Field(default=None, max_length=64)
    uom: str | None = Field(default=None, max_length=64)
    quantity: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    rate: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    amount: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)

    @model_validator(mode="after")
    def validate_new_line(self) -> InvoiceLineCorrection:
        if self.id is None and not any(
            value not in (None, "")
            for value in (
                self.description,
                self.item_code,
                self.hsn_sac,
                self.quantity,
                self.rate,
                self.amount,
            )
        ):
            raise ValueError("A new line needs at least one value.")
        return self


class AiValueRefBody(BaseModel):
    """One value read by AI: a header field, or a field of the line with this id."""

    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(min_length=1, max_length=64)
    line_id: UUID | None = None


class VerifyAiValuesBody(BaseModel):
    """Verify the listed values, or every value whose OCR cross-check agrees (PD-4d)."""

    model_config = ConfigDict(extra="forbid")

    values: list[AiValueRefBody] = Field(default_factory=list, max_length=5_000)
    all_agreeing: bool = False

    @model_validator(mode="after")
    def _one_choice(self) -> VerifyAiValuesBody:
        if bool(self.values) == self.all_agreeing:
            raise ValueError("send either values or all_agreeing, not both")
        return self


class _CorrectionBodyBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_no: str | None = Field(default=None, max_length=256)
    doc_date: date | None = None
    vendor_name: str | None = Field(default=None, max_length=1_000)
    vendor_gstin: str | None = Field(default=None, max_length=32)
    buyer_gstin: str | None = Field(default=None, max_length=32)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    taxable_total: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    cgst: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    sgst: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    igst: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    round_off: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    grand_total: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    revision: str | None = Field(default=None, max_length=256)
    status: Literal["open", "closed", "cancelled"] | None = None
    ship_to: str | None = Field(default=None, max_length=1_000)
    payment_terms: str | None = Field(default=None, max_length=1_000)
    delivery_date: date | None = None
    po_refs: list[str] | None = Field(default=None, max_length=50)
    grn_refs: list[str] | None = Field(default=None, max_length=50)
    challan_no: str | None = Field(default=None, max_length=256)
    e_way_bill_no: str | None = Field(default=None, max_length=256)
    irn: str | None = Field(default=None, max_length=256)
    place_of_supply: str | None = Field(default=None, max_length=256)
    verified: bool = False
    reason: str | None = Field(default=None, max_length=500)
    ai_suggestion_refs: list[AiSuggestionRefBody] = Field(default_factory=list, max_length=200)


class InvoiceCorrectionBody(_CorrectionBodyBase):
    lines: list[InvoiceLineCorrection] | None = Field(default=None, max_length=500)


class DocumentCorrectionBody(_CorrectionBodyBase):
    """Typed superset for the existing invoice, PO and GRN correction route."""

    revision: str | None = Field(default=None, max_length=256)
    status: Literal["open", "closed", "cancelled"] | None = None
    ship_to: str | None = Field(default=None, max_length=1_000)
    payment_terms: str | None = Field(default=None, max_length=1_000)
    delivery_date: date | None = None
    supplier_invoice_no: str | None = Field(default=None, max_length=256)
    lines: list[DocumentLineCorrection] | None = Field(default=None, max_length=500)


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


def _service(request: Request) -> IngestionService:
    service = getattr(request.app.state, "ingestion_service", None)
    if not isinstance(service, IngestionService):
        raise RuntimeError("ingestion service is unavailable")
    return service


def _handle_ingestion_error(exc: Exception, request: Request) -> DecimalJSONResponse:
    if isinstance(exc, BatchVersionConflict):
        return _problem(
            request,
            status=409,
            title="Resource changed",
            detail="The resource changed after it was loaded. Reload the current state.",
            code="version_conflict",
        )
    if isinstance(exc, IngestionFailure):
        status = (
            404 if exc.code.endswith("NOT_FOUND") else 409 if exc.code.endswith("PENDING") else 422
        )
        return _problem(
            request,
            status=status,
            title="Ingestion request failed",
            detail=str(exc),
            code=exc.code.lower(),
        )
    if isinstance(exc, ObjectStoreUnavailable):
        return _problem(
            request,
            status=503,
            title="Object storage unavailable",
            detail="The file could not be stored or read right now.",
            code="object_store_unavailable",
            headers={"Retry-After": "2"},
        )
    raise exc


def _problem(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    code: str,
    headers: dict[str, str] | None = None,
) -> DecimalJSONResponse:
    from reconbench_app.api.conventions import problem_response

    return problem_response(
        status=status,
        title=title,
        detail=detail,
        instance=request.url.path,
        code=code,
        headers=headers,
    )


def _public_file(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "id",
        "workspace_id",
        "batch_id",
        "parent_file_id",
        "sha256",
        "display_name",
        "archive_path",
        "zone",
        "zone_uploaded",
        "format",
        "sniffed_type",
        "mismatch_state",
        "status",
        "error_code",
        "error_message",
        "attempt_count",
        "size_bytes",
        "page_count",
        "layout_signature",
        "parser_version",
        "docs_found",
        "docs_new",
        "docs_updated",
        "docs_skipped",
        "reupload_skips",
        "retry_available",
        "retry_reason",
        "uploaded_at",
        "queued_at",
        "parse_started_at",
        "parse_finished_at",
        "removed_at",
        "version",
    )
    return {name: row[name] for name in fields if name in row}


def _etag_for_payload(value: Any) -> str:
    digest = hashlib.sha256(
        json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f'"{digest}"'


def _content_stream(read: ObjectRead) -> Iterator[bytes]:
    try:
        while chunk := read.stream.read(64 * 1024):
            yield chunk
    finally:
        close = getattr(read.stream, "close", None)
        if callable(close):
            close()


@router.post(
    "/batches",
    status_code=201,
    tags=["Ingestion"],
    responses={401: {"model": ProblemDetails}, 403: {"model": ProblemDetails}},
)
@requires("files.upload")
def create_batch(body: BatchBody, request: Request) -> Response:
    try:
        row = _service(request).create_batch(
            _actor(request),
            label=body.label,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    content = to_jsonable(row)
    return DecimalJSONResponse(
        status_code=201,
        content=content,
        headers={"ETag": etag_for_version(int(row["version"]))},
    )


@router.get("/batches", tags=["Ingestion"])
@requires("data.view")
def get_batches(request: Request) -> Response:
    rows = _service(request).list_batches(_actor(request).workspace_id)
    return DecimalJSONResponse(content=to_jsonable(rows), headers={"ETag": _etag_for_payload(rows)})


@router.patch(
    "/batches/{batch_id}",
    tags=["Ingestion"],
    responses={
        400: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        428: {"model": ProblemDetails},
    },
)
@requires("files.upload")
def patch_batch(
    batch_id: UUID,
    body: BatchBody,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    expected = require_if_match(if_match)
    try:
        row = _service(request).update_batch(
            _actor(request),
            batch_id,
            expected_version=expected,
            label=body.label,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    if row is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The batch was not found.",
            code="not_found",
        )
    return DecimalJSONResponse(
        content=to_jsonable(row),
        headers={"ETag": etag_for_version(int(row["version"]))},
    )


@router.post(
    "/files",
    status_code=201,
    tags=["Ingestion"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
    responses={
        404: {"model": ProblemDetails},
        413: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
        503: {"model": ProblemDetails},
    },
)
@requires("files.upload")
async def upload_file(
    request: Request,
    batch_id: UUID = Query(),  # noqa: B008 - FastAPI query metadata is required here.
    zone: UploadZone = Query(),  # noqa: B008 - FastAPI query metadata is required here.
    file_name: str = Header(alias="X-File-Name"),
) -> Response:
    service = _service(request)
    actor = _actor(request)
    try:
        limits = await run_in_threadpool(service.limits, actor.workspace_id)
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as content:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                digest.update(chunk)
                if len(prefix) < 4096:
                    prefix.extend(chunk[: 4096 - len(prefix)])
                if size <= limits.max_file_bytes:
                    content.write(chunk)
            content.seek(0)
            receipt = await run_in_threadpool(
                service.receive_file,
                actor,
                batch_id=batch_id,
                zone=zone,
                display_name=safe_display_name(file_name),
                size_bytes=size,
                sha256=digest.hexdigest(),
                file_format=sniff_format(bytes(prefix)),
                stream=content,
            )
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    payload = {
        "file_id": receipt.file_id,
        "batch_id": batch_id,
        "sha256": receipt.sha256,
        "size_bytes": receipt.size_bytes,
        "format": receipt.format,
        "status": receipt.status,
        "duplicate": receipt.duplicate,
        "reactivated": receipt.reactivated,
        # A duplicate is not stored again; the UI links to the original upload (PD-0b-4).
        "original_display_name": receipt.original_display_name,
        "original_batch_id": receipt.original_batch_id,
    }
    status = 200 if receipt.duplicate else 201
    return DecimalJSONResponse(status_code=status, content=payload)


@router.post("/batches/{batch_id}/process", status_code=202, tags=["Ingestion"])
@requires("files.upload")
def process_batch(batch_id: UUID, request: Request) -> Response:
    try:
        queued = _service(request).queue_files(
            _actor(request),
            batch_id=batch_id,
        )
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    return DecimalJSONResponse(
        status_code=202,
        content={"batch_id": batch_id, "queued_count": len(queued), "jobs": queued},
    )


@router.get("/files", tags=["Ingestion"])
@requires("data.view")
def get_files(
    request: Request,
    batch_id: UUID | None = Query(default=None),  # noqa: B008 - FastAPI query metadata.
    limit: int = Query(default=100, ge=1, le=500),  # noqa: B008 - FastAPI query metadata.
) -> Response:
    rows = _service(request).list_files(
        _actor(request).workspace_id, batch_id=batch_id, limit=limit
    )
    public_rows = [_public_file(row) for row in rows]
    return conditional_json_response(request, public_rows, _etag_for_payload(public_rows))


@router.get("/files/{file_id}", tags=["Ingestion"])
@requires("data.view")
def get_file(file_id: UUID, request: Request) -> Response:
    row = _service(request).get_file(_actor(request).workspace_id, file_id)
    if row is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The file was not found.",
            code="not_found",
        )
    return conditional_json_response(
        request,
        _public_file(row),
        etag_for_version(int(row["version"])),
    )


@router.patch(
    "/files/{file_id}",
    tags=["Ingestion"],
    responses={409: {"model": ProblemDetails}, 428: {"model": ProblemDetails}},
)
@requires("files.upload")
def resolve_file_type(
    file_id: UUID,
    body: MismatchDecision,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    expected = require_if_match(if_match)
    try:
        row = _service(request).resolve_type_mismatch(
            _actor(request),
            file_id,
            accept_suggested=body.decision == "accept_suggested",
            expected_version=expected,
        )
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    return DecimalJSONResponse(
        content=_public_file(row),
        headers={"ETag": etag_for_version(int(row["version"]))},
    )


@router.post(
    "/files/{file_id}/page-split",
    status_code=202,
    tags=["Ingestion"],
    responses={409: {"model": ProblemDetails}, 428: {"model": ProblemDetails}},
)
@requires("files.upload")
def resolve_page_split(
    file_id: UUID,
    body: PageSplitDecision,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """Accept a PDF's proposed split into documents, or keep it as one document."""

    expected = require_if_match(if_match)
    try:
        row = _service(request).resolve_page_split(
            _actor(request),
            file_id,
            accept=body.decision == "accept_split",
            expected_version=expected,
        )
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    return DecimalJSONResponse(
        status_code=202,
        content=_public_file(row),
        headers={"ETag": etag_for_version(int(row["version"]))},
    )


@router.get("/duplicate-holds", tags=["Ingestion"])
@requires("data.view")
def get_duplicate_holds(
    request: Request,
    file_id: UUID | None = Query(default=None),  # noqa: B008 - FastAPI query metadata.
    state: str | None = Query(default=None, pattern=r"^(open|discarded|replaced|kept)$"),  # noqa: B008
) -> Response:
    """Uploads not ingested because they match an active invoice (PD-0b-4)."""

    rows = _service(request).list_duplicate_holds(
        _actor(request).workspace_id, file_id=file_id, state=state
    )
    return DecimalJSONResponse(content=to_jsonable(rows))


@router.post("/duplicate-holds/{hold_id}/decision", tags=["Ingestion"])
@requires("files.upload")
def decide_duplicate_hold(
    hold_id: UUID,
    body: DuplicateDecisionBody,
    request: Request,
) -> Response:
    expected_version = require_if_match(request.headers.get("if-match"))
    try:
        result = _service(request).decide_duplicate_hold(
            _actor(request),
            hold_id,
            expected_version=expected_version,
            decision=body.decision,
        )
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    if result is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The duplicate was not found.",
            code="not_found",
        )
    return DecimalJSONResponse(
        content=to_jsonable(result), headers={"ETag": etag_for_version(int(result["version"]))}
    )


@router.post("/files/{file_id}/retry", status_code=202, tags=["Ingestion"])
@requires("files.upload")
def retry_file(file_id: UUID, request: Request) -> Response:
    try:
        result = _service(request).retry_file(_actor(request), file_id)
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    return DecimalJSONResponse(status_code=202, content=result)


@router.post("/files/{file_id}/column-mapping", status_code=202, tags=["Ingestion"])
@requires("files.upload")
def save_csv_column_mapping(
    file_id: UUID,
    body: ColumnMappingBody,
    request: Request,
) -> Response:
    try:
        result = _service(request).save_csv_mapping(
            _actor(request), file_id, column_mapping=body.column_mapping
        )
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    return DecimalJSONResponse(status_code=202, content=result)


@router.delete("/files/{file_id}", status_code=204, tags=["Ingestion"])
@requires("files.upload")
def delete_file(file_id: UUID, request: Request) -> Response:
    try:
        _service(request).soft_delete(_actor(request), file_id)
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    return Response(status_code=204)


@router.get("/files/{file_id}/content", tags=["Ingestion"])
@requires("data.view")
def file_content(
    file_id: UUID,
    request: Request,
    range_header: str | None = Header(default=None, alias="Range"),
) -> Response:
    service = _service(request)
    row = service.get_file(_actor(request).workspace_id, file_id)
    if row is None or row["status"] == "removed":
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The file was not found.",
            code="not_found",
        )
    try:
        object_read = service.object_store.get(row["object_key"], range_header)
    except ValueError:
        return _problem(
            request,
            status=416,
            title="Range not satisfiable",
            detail="The requested byte range is invalid.",
            code="range_not_satisfiable",
        )
    except Exception:
        return _problem(
            request,
            status=503,
            title="Object storage unavailable",
            detail="The file content could not be read right now.",
            code="object_store_unavailable",
        )
    media_type = {
        "pdf": "application/pdf",
        "xml": "application/xml",
        "csv": "text/csv; charset=utf-8",
        "zip": "application/zip",
    }.get(str(row["format"]), "application/octet-stream")
    headers = {
        "Accept-Ranges": "bytes",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(str(row['display_name']))}",
    }
    if object_read.content_range:
        headers["Content-Range"] = object_read.content_range
    return StreamingResponse(
        _content_stream(object_read),
        status_code=object_read.status_code,
        media_type=media_type,
        headers=headers,
    )


@router.get("/files/{file_id}/content-link", tags=["Ingestion"])
@requires("data.view")
def file_content_link(file_id: UUID, request: Request) -> Response:
    service = _service(request)
    row = service.get_file(_actor(request).workspace_id, file_id)
    if row is None or row["status"] == "removed":
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The file was not found.",
            code="not_found",
        )
    try:
        url = service.object_store.presigned_get(str(row["object_key"]), expires_seconds=60)
    except NotImplementedError:
        return _problem(
            request,
            status=501,
            title="Unavailable",
            detail="Signed links are not supported by this storage adapter.",
            code="signed_link_unavailable",
        )
    except Exception as exc:
        del exc
        return _problem(
            request,
            status=503,
            title="Object storage unavailable",
            detail="The signed file link could not be created right now.",
            code="object_store_unavailable",
        )
    return DecimalJSONResponse(content={"url": url, "expires_in": 60})


@router.get("/files/{file_id}/errors", tags=["Ingestion"])
@requires("data.view")
def get_file_errors(file_id: UUID, request: Request) -> Response:
    rows = _service(request).file_errors(_actor(request).workspace_id, file_id)
    if rows is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The file was not found.",
            code="not_found",
        )
    lines = "".join(
        json.dumps(to_jsonable(row), sort_keys=True, separators=(",", ":")) + "\n" for row in rows
    )
    return Response(content=lines, media_type="application/x-ndjson; charset=utf-8")


@router.get("/batches/{batch_id}/summary", tags=["Ingestion"])
@requires("data.view")
def batch_summary(batch_id: UUID, request: Request) -> Response:
    result = _service(request).batch_summary(_actor(request).workspace_id, batch_id)
    if result is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The batch was not found.",
            code="not_found",
        )
    return DecimalJSONResponse(
        content=to_jsonable(result), headers={"ETag": _etag_for_payload(result)}
    )


@router.get("/documents", tags=["Documents"])
@requires("data.view")
def get_documents(
    request: Request,
    doc_type: str | None = Query(default=None, pattern=r"^(po|grn|invoice)$"),
    batch_id: UUID | None = Query(default=None),  # noqa: B008 - FastAPI query metadata.
    source_file_id: UUID | None = Query(default=None),  # noqa: B008 - FastAPI query metadata.
    limit: int = Query(default=100, ge=1, le=500),  # noqa: B008 - FastAPI query metadata.
) -> Response:
    rows = _service(request).list_documents(
        _actor(request).workspace_id,
        doc_type=doc_type,
        batch_id=batch_id,
        source_file_id=source_file_id,
        limit=limit,
    )
    return DecimalJSONResponse(content=to_jsonable(rows), headers={"ETag": _etag_for_payload(rows)})


@router.get("/documents/{document_id}", tags=["Documents"])
@requires("data.view")
def get_document(document_id: UUID, request: Request) -> Response:
    result = _service(request).get_document(_actor(request).workspace_id, document_id)
    if result is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The document was not found.",
            code="not_found",
        )
    return DecimalJSONResponse(
        content=to_jsonable(result),
        headers={"ETag": etag_for_version(int(result["document"]["version"]))},
    )


@router.post("/files/{source_file_id}/manual-invoice", tags=["Documents"])
@requires("files.upload")
def create_manual_invoice(
    source_file_id: UUID,
    body: InvoiceCorrectionBody,
    request: Request,
) -> Response:
    if body.ai_suggestion_refs:
        return _problem(
            request,
            status=422,
            title="Ingestion request failed",
            detail="AI suggestion references apply only to corrections of a parsed PDF.",
            code="ai_suggestion_ref_invalid",
        )
    try:
        result = _service(request).create_manual_invoice(
            _actor(request),
            source_file_id,
            header_patch=body.model_dump(
                exclude_unset=True,
                exclude={"lines", "verified", "reason", "ai_suggestion_refs"},
                mode="python",
            ),
            lines_patch=(
                [line.model_dump(exclude_unset=True, mode="python") for line in body.lines]
                if body.lines is not None
                else []
            ),
            verified=body.verified,
            reason=body.reason,
        )
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    if result is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The invoice source file was not found.",
            code="not_found",
        )
    return DecimalJSONResponse(
        status_code=201,
        content=to_jsonable(result),
        headers={"ETag": etag_for_version(int(result["document"]["version"]))},
    )


@router.put(
    "/documents/{document_id}/correction",
    tags=["Documents"],
    summary="Correct Document",
)
@requires("files.upload")
def correct_invoice_document(
    document_id: UUID,
    body: DocumentCorrectionBody,
    request: Request,
) -> Response:
    expected_version = require_if_match(request.headers.get("if-match"))
    try:
        result = _service(request).correct_invoice_document(
            _actor(request),
            document_id,
            expected_version=expected_version,
            header_patch=body.model_dump(
                exclude_unset=True,
                exclude={"lines", "verified", "reason", "ai_suggestion_refs"},
                mode="python",
            ),
            lines_patch=(
                [line.model_dump(exclude_unset=True, mode="python") for line in body.lines]
                if "lines" in body.model_fields_set and body.lines is not None
                else None
            ),
            verified=body.verified,
            reason=body.reason,
            ai_suggestion_refs=[
                AiSuggestionRef(
                    ai_call_id=ref.ai_call_id,
                    scope=ref.scope,
                    line_no=ref.line_no,
                    field=ref.field,
                )
                for ref in body.ai_suggestion_refs
            ],
        )
    except (BatchVersionConflict, IngestionFailure, ObjectStoreUnavailable) as exc:
        return _handle_ingestion_error(exc, request)
    if result is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The document was not found.",
            code="not_found",
        )
    return DecimalJSONResponse(
        content=to_jsonable(result),
        headers={"ETag": etag_for_version(int(result["document"]["version"]))},
    )


@router.post(
    "/documents/{document_id}/verify-ai-values",
    tags=["Documents"],
    summary="Verify AI-read values",
)
@requires("files.upload")
def verify_ai_values(
    document_id: UUID,
    body: VerifyAiValuesBody,
    request: Request,
) -> Response:
    """Record that a person checked values read by AI against the page (PD-4d)."""

    expected_version = require_if_match(request.headers.get("if-match"))
    try:
        result = _service(request).verify_ai_values(
            _actor(request),
            document_id,
            expected_version=expected_version,
            values=[(item.line_id, item.field_name) for item in body.values],
            all_agreeing=body.all_agreeing,
        )
    except (BatchVersionConflict, IngestionFailure) as exc:
        return _handle_ingestion_error(exc, request)
    if result is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The document was not found.",
            code="not_found",
        )
    return DecimalJSONResponse(
        content=to_jsonable(result),
        headers={"ETag": etag_for_version(int(result["document"]["version"]))},
    )


@router.get("/documents/{document_id}/ai-suggestions", tags=["Documents"])
@requires("data.view")
def get_document_ai_suggestions(document_id: UUID, request: Request) -> Response:
    """Advisory H1 or vision (page-image) suggestions, checked again; never applied here."""

    result = _service(request).ai_suggestions(_actor(request).workspace_id, document_id)
    if result is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The document was not found.",
            code="not_found",
        )
    return DecimalJSONResponse(
        content=to_jsonable(result),
        headers={"ETag": etag_for_version(int(result["document_version"]))},
    )


@router.get("/documents/{document_id}/raw", tags=["Documents"])
@requires("data.view")
def get_document_raw(document_id: UUID, request: Request) -> Response:
    service = _service(request)
    source = service.document_source(_actor(request).workspace_id, document_id)
    if source is None:
        return _problem(
            request,
            status=404,
            title="Not found",
            detail="The document was not found.",
            code="not_found",
        )
    try:
        object_read = service.object_store.get(source["object_key"])
    except Exception as exc:
        del exc
        return _problem(
            request,
            status=503,
            title="Object storage unavailable",
            detail="The source file could not be read right now.",
            code="object_store_unavailable",
        )
    media_type = {
        "pdf": "application/pdf",
        "xml": "application/xml",
        "csv": "text/csv; charset=utf-8",
        "zip": "application/zip",
    }.get(str(source["format"]), "application/octet-stream")
    return StreamingResponse(
        _content_stream(object_read),
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(str(source['display_name']))}",
        },
    )


@router.get("/admin/errors", tags=["Operations"])
@requires("data.view")
def admin_errors(request: Request) -> Response:
    result = _service(request).admin_errors(_actor(request).workspace_id)
    return DecimalJSONResponse(
        content=to_jsonable(result), headers={"ETag": _etag_for_payload(result)}
    )


__all__ = ["BatchBody", "ColumnMappingBody", "MismatchDecision", "router"]
