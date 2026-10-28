"""Workspace-scoped upload, archive, parse, and document persistence services."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import shutil
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO, Protocol, cast
from uuid import UUID

import sqlalchemy as sa
from botocore.exceptions import BotoCoreError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from reconbench_core.extraction.pages import split_pages
from reconbench_core.extraction.vision import align_lines as align_vision_lines
from reconbench_core.normalize import (
    canonicalize_document_number,
    normalize_item_text,
    normalize_uom,
    normalize_vendor_name,
)
from reconbench_core.parsers import ParseResult, ParserHints, parse
from reconbench_core.parsers.common import arithmetic_failures
from reconbench_core.parsers.parties import party_hints_from_config
from reconbench_core.parsers.types import PageImageHash, PageSplit, ParseWarning
from reconbench_core.schema import (
    DocLine,
    DocType,
    Document,
    ExtractionMethod,
    FieldEvidence,
    FieldOrigin,
    FieldSource,
    FieldValue,
    FieldVerification,
    FileFormat,
    InvoiceHeader,
    UploadZone,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from reconbench_app.ai import h1, shadow, vision
from reconbench_app.ai.contracts import (
    FieldKind,
    H1Output,
    InvalidOutput,
    VisionOutput,
    typed_value,
)
from reconbench_app.ai.limits import AiLimits, LimitsState
from reconbench_app.ai.validators import GroundedSuggestion, validate_h1
from reconbench_app.audit import write_audit_event
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.document_identity import (
    content_fingerprint,
    document_identity_collision_key,
    document_natural_key,
    identity_payload,
    page_hashes_within_distance,
    rescan_identity_payload,
    verified_field_confidence,
)
from reconbench_app.duplicates import (
    active_structured_by_number,
    doc_no_text,
    find_invoice_duplicate,
    hold_for,
)
from reconbench_app.extraction.engine import DEFAULT_ENGINE
from reconbench_app.extraction.vision_pages import vision_pages_isolated
from reconbench_app.pdf_sandbox import parse_pdf_isolated
from reconbench_app.storage import ObjectStore

if TYPE_CHECKING:
    from reconbench_app.ai.enqueue import AiHookEnqueuer

_LOGGER = logging.getLogger(__name__)
PARSER_VERSION = "phase4-parser-7"  # 7: native Tally exports (WI-IMP1, D95)
_MAX_DISPLAY_NAME = 255
_READ_CHUNK_BYTES = 64 * 1024
_CSV_MAPPING_FIELDS = frozenset(
    {
        "doc_type",
        "doc_no",
        "doc_date",
        "vendor_name",
        "vendor_gstin",
        "buyer_gstin",
        "currency",
        "taxable_total",
        "cgst",
        "sgst",
        "igst",
        "round_off",
        "grand_total",
        "revision",
        "status",
        "ship_to",
        "payment_terms",
        "delivery_date",
        "po_refs",
        "grn_refs",
        "challan_no",
        "supplier_invoice_no",
        "e_way_bill_no",
        "irn",
        "place_of_supply",
        "line_no",
        "line_type",
        "description",
        "item_code",
        "hsn_sac",
        "uom",
        "quantity",
        "received_qty",
        "rejected_qty",
        "accepted_qty",
        "rate",
        "discount",
        "amount",
        "tax_rate",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "po_ref",
        "po_line_ref",
    }
)
# Invoice fields a correction may change, mapped to their ``document_version`` /
# ``doc_line`` columns. The H1 invoice allowlists in ``ai.contracts`` match these.
INVOICE_HEADER_COLUMNS: Mapping[str, str] = {
    "doc_no": "doc_no",
    "doc_date": "doc_date",
    "vendor_name": "vendor_name",
    "vendor_gstin": "vendor_gstin",
    "buyer_gstin": "buyer_gstin",
    "currency": "currency",
    "taxable_total": "taxable_total",
    "cgst": "cgst_total",
    "sgst": "sgst_total",
    "igst": "igst_total",
    "round_off": "round_off",
    "grand_total": "grand_total",
    "po_refs": "po_refs",
    "grn_refs": "grn_refs",
    "challan_no": "challan_no",
    "e_way_bill_no": "e_way_bill_no",
    "irn": "irn",
    "place_of_supply": "place_of_supply",
}
INVOICE_LINE_COLUMNS: Mapping[str, str] = {
    "description": "description",
    "item_code": "item_code",
    "hsn_sac": "hsn_sac",
    "uom": "uom_as_read",
    "quantity": "quantity",
    "rate": "rate",
    "amount": "amount",
}
_COMMON_CORRECTION_HEADER_COLUMNS: Mapping[str, str] = {
    "doc_no": "doc_no",
    "doc_date": "doc_date",
    "vendor_name": "vendor_name",
    "vendor_gstin": "vendor_gstin",
    "buyer_gstin": "buyer_gstin",
    "currency": "currency",
    "taxable_total": "taxable_total",
    "cgst": "cgst_total",
    "sgst": "sgst_total",
    "igst": "igst_total",
    "round_off": "round_off",
    "grand_total": "grand_total",
}
_PO_CORRECTION_HEADER_COLUMNS: Mapping[str, str] = {
    **_COMMON_CORRECTION_HEADER_COLUMNS,
    "revision": "revision",
    "status": "po_status",
    "ship_to": "ship_to",
    "payment_terms": "payment_terms_text",
    "delivery_date": "delivery_date",
}
_GRN_CORRECTION_HEADER_COLUMNS: Mapping[str, str] = {
    **_COMMON_CORRECTION_HEADER_COLUMNS,
    "po_refs": "po_refs",
    "challan_no": "challan_no",
    "supplier_invoice_no": "supplier_invoice_no",
}
_CORRECTION_HEADER_COLUMNS_BY_TYPE: Mapping[str, Mapping[str, str]] = {
    DocType.INVOICE.value: INVOICE_HEADER_COLUMNS,
    DocType.PO.value: _PO_CORRECTION_HEADER_COLUMNS,
    DocType.GRN.value: _GRN_CORRECTION_HEADER_COLUMNS,
}
_PO_CORRECTION_LINE_COLUMNS: Mapping[str, str] = {
    "line_type": "line_type",
    "description": "description",
    "item_code": "item_code",
    "hsn_sac": "hsn_sac",
    "uom": "uom_as_read",
    "quantity": "quantity",
    "rate": "rate",
    "discount": "discount",
    "amount": "amount",
    "tax_rate": "tax_rate",
    "cgst_amount": "cgst_amount",
    "sgst_amount": "sgst_amount",
    "igst_amount": "igst_amount",
    "po_ref": "po_ref",
    "po_line_ref": "po_line_ref",
}
_GRN_CORRECTION_LINE_COLUMNS: Mapping[str, str] = {
    "line_type": "line_type",
    "description": "description",
    "item_code": "item_code",
    "hsn_sac": "hsn_sac",
    "uom": "uom_as_read",
    "quantity": "quantity",
    "received_qty": "received_qty",
    "rejected_qty": "rejected_qty",
    "accepted_qty": "accepted_qty",
    "rate": "rate",
    "discount": "discount",
    "amount": "amount",
    "tax_rate": "tax_rate",
    "cgst_amount": "cgst_amount",
    "sgst_amount": "sgst_amount",
    "igst_amount": "igst_amount",
    "po_ref": "po_ref",
    "po_line_ref": "po_line_ref",
}
_CORRECTION_LINE_COLUMNS_BY_TYPE: Mapping[str, Mapping[str, str]] = {
    DocType.INVOICE.value: INVOICE_LINE_COLUMNS,
    DocType.PO.value: _PO_CORRECTION_LINE_COLUMNS,
    DocType.GRN.value: _GRN_CORRECTION_LINE_COLUMNS,
}
_MANUAL_SNIPPET = "Entered by reviewer; not quoted from the PDF source."
# The invoice values the extraction gate reads (Document.confidence). Marking an invoice
# reviewed verifies each one the person was shown (P2, PD-2b).
_REVIEWED_HEADER_FIELDS = frozenset(
    {"doc_no", "doc_date", "grand_total", "vendor_name", "vendor_gstin"}
)
_REVIEWED_LINE_FIELDS = frozenset({"quantity", "rate", "amount"})
_AI_SNIPPET_LIMIT = 500
_CORRECTION_QA_CODE = "CORRECTION_QA_FAILED"


def _correction_header_columns(doc_type: str) -> Mapping[str, str]:
    return _CORRECTION_HEADER_COLUMNS_BY_TYPE.get(doc_type, {})


def _correction_line_columns(doc_type: str) -> Mapping[str, str]:
    return _CORRECTION_LINE_COLUMNS_BY_TYPE.get(doc_type, {})


@dataclass(frozen=True, slots=True)
class AiSuggestionRef:
    """A correction value the reviewer took from an H1 suggestion."""

    ai_call_id: UUID
    scope: str
    line_no: int | None
    field: str


def _parser_refresh_required(file_row: Mapping[str, Any]) -> bool:
    """Whether a parsed file should be offered a safe re-run with the current parser."""

    return file_row.get("status") == "parsed" and file_row.get("parser_version") != PARSER_VERSION


class IngestionLimits(BaseModel):
    """Protective limits loaded from the active workspace app.limits config."""

    model_config = ConfigDict(extra="forbid", strict=True)

    max_file_mb: int = Field(ge=1)
    max_pages: int = Field(ge=1)
    zip_max_entries: int = Field(ge=1)
    zip_max_uncompressed_gb: int = Field(ge=1)
    zip_max_ratio: int = Field(ge=1)

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    @property
    def max_zip_uncompressed_bytes(self) -> int:
        return self.zip_max_uncompressed_gb * 1024**3


def parser_hints_from_config(
    body: Mapping[str, Any],
    *,
    zone_type: DocType | None,
    file_format: FileFormat,
    limits: IngestionLimits,
    page_split: PageSplit = PageSplit.AUTO,
    column_mapping: Mapping[str, str] | None = None,
) -> ParserHints:
    """Parser hints from a config body; upload and the accuracy bench both build them here."""

    engine_body = body.get("engine", {})
    synonyms = engine_body.get("synonyms", {}) if isinstance(engine_body, Mapping) else {}
    uom = engine_body.get("uom", {}) if isinstance(engine_body, Mapping) else {}
    policies = engine_body.get("charge_keywords", []) if isinstance(engine_body, Mapping) else []
    return ParserHints(
        doc_type=zone_type,
        file_format=file_format,
        synonyms=_parser_synonym_overrides(synonyms),
        column_mapping=dict(column_mapping or {}),
        uom_aliases=dict(uom.get("aliases", {})) if isinstance(uom, Mapping) else {},
        charge_keywords=tuple(policies) if policies else ParserHints().charge_keywords,
        max_bytes=limits.max_file_bytes,
        max_pages=limits.max_pages,
        page_split=page_split,
        **(party_hints_from_config(engine_body) if isinstance(engine_body, Mapping) else {}),
    )


class IngestionFailure(RuntimeError):
    """A deterministic input or workflow failure with a stable reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BatchVersionConflict(RuntimeError):
    """A batch edit used an outdated If-Match version."""


class ObjectStoreUnavailable(RuntimeError):
    """An object-store read/write failed and may succeed on infrastructure retry."""


class QueuePort(Protocol):
    """Queue adapter with transactional task insertion."""

    def enqueue(self, task_name: str, *, file_id: UUID, connection: Connection) -> int: ...


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    file_id: UUID
    sha256: str
    size_bytes: int
    format: str
    status: str
    duplicate: bool = False
    reactivated: bool = False
    # For a duplicate: the original upload a person can open (PD-0b-4).
    original_display_name: str | None = None
    original_batch_id: UUID | None = None


def safe_display_name(value: str) -> str:
    """Keep a user-facing basename; never use it as an object-store path."""

    normalized = value.replace("\\", "/").split("/")[-1]
    normalized = "".join(char for char in normalized if char >= " " and char != "\x7f")
    normalized = normalized.strip().strip(".") or "upload"
    return normalized[:_MAX_DISPLAY_NAME]


def sniff_format(prefix: bytes) -> FileFormat:
    """Identify supported containers from content signatures, never from filenames."""

    probe = prefix.removeprefix(b"\xef\xbb\xbf").lstrip()
    if probe.startswith(b"%PDF-"):
        return FileFormat.PDF
    if probe.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return FileFormat.ZIP
    if probe.startswith(b"<"):
        return FileFormat.XML
    if probe.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            if probe.decode("utf-16").lstrip().startswith("<"):
                return FileFormat.XML
        except UnicodeDecodeError:
            pass
    elif probe.startswith(b"<\x00") or probe.startswith(b"\x00<"):
        encoding = "utf-16-le" if probe.startswith(b"<\x00") else "utf-16-be"
        try:
            if probe.decode(encoding).lstrip().startswith("<"):
                return FileFormat.XML
        except UnicodeDecodeError:
            pass
    # CSV is the text-table fallback. The parser validates encoding and structure.
    return FileFormat.CSV


def _as_connection(connection: Connection) -> Any:
    """Return SQLAlchemy's underlying psycopg connection for atomic queue insertion."""

    return connection.connection.driver_connection


def _event(
    connection: Connection,
    *,
    workspace_id: UUID,
    file_id: UUID,
    stage: str,
    outcome: str,
    error_code: str | None = None,
    warning_codes: tuple[str, ...] = (),
    duration_ms: int | None = None,
    parser_version: str | None = None,
    job_id: int | None = None,
    detail: Mapping[str, Any] | None = None,
    document_id: UUID | None = None,
    document_version_id: UUID | None = None,
) -> None:
    table = metadata.tables["processing_event"]
    connection.execute(
        sa.insert(table).values(
            id=new_id(),
            workspace_id=workspace_id,
            source_file_id=file_id,
            document_id=document_id,
            document_version_id=document_version_id,
            stage=stage,
            outcome=outcome,
            duration_ms=duration_ms,
            parser_version=parser_version,
            warning_codes=list(warning_codes),
            error_code=error_code,
            job_id=job_id,
            detail=dict(detail) if detail is not None else None,
        )
    )


class IngestionService:
    """Application services for ingestion and its background task handlers."""

    def __init__(
        self,
        engine: Engine,
        object_store: ObjectStore,
        queue: QueuePort,
        *,
        ocr_languages: tuple[str, ...] = ("eng",),
        einvoice_keys: tuple[Any, ...] = (),
        extraction_engine: str = DEFAULT_ENGINE,
        ai_hooks: AiHookEnqueuer | None = None,
        ai_limits: Callable[[], LimitsState] | None = None,
    ) -> None:
        self.engine = engine
        self.object_store = object_store
        self.queue = queue
        self.ocr_languages = ocr_languages
        self.einvoice_keys = einvoice_keys
        self.extraction_engine = extraction_engine
        self.ai_hooks = ai_hooks
        self.ai_limits = ai_limits or (lambda: LimitsState(AiLimits()))

    @requires("data.view")
    def limits(self, workspace_id: UUID) -> IngestionLimits:
        active = metadata.tables["config_active"]
        version = metadata.tables["config_version"]
        with self.engine.connect() as connection:
            body = connection.scalar(
                sa.select(version.c.body)
                .select_from(
                    active.join(
                        version,
                        sa.and_(
                            active.c.workspace_id == version.c.workspace_id,
                            active.c.config_version_id == version.c.id,
                        ),
                    )
                )
                .where(active.c.workspace_id == workspace_id)
            )
        if not isinstance(body, Mapping) or not isinstance(body.get("app"), Mapping):
            raise RuntimeError("active ingestion configuration is unavailable")
        app_body = cast(Mapping[str, Any], body["app"])
        raw_limits = app_body.get("limits")
        if not isinstance(raw_limits, Mapping):
            raise RuntimeError("active app.limits configuration is unavailable")
        try:
            return IngestionLimits.model_validate(dict(raw_limits))
        except ValidationError as exc:
            raise RuntimeError("active app.limits configuration is invalid") from exc

    @requires("files.upload")
    def create_batch(
        self,
        actor: ActorContext,
        *,
        label: str | None,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> dict[str, Any]:
        table = metadata.tables["batch"]
        batch_id = new_id()
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.insert(table)
                    .values(
                        id=batch_id,
                        workspace_id=actor.workspace_id,
                        label=label.strip() if label and label.strip() else None,
                        label_is_suggested=not bool(label and label.strip()),
                        period_start=period_start,
                        period_end=period_end,
                        created_by=actor.actor_id,
                    )
                    .returning(*table.c)
                )
                .mappings()
                .one()
            )
        return dict(row)

    @requires("data.view")
    def list_batches(self, workspace_id: UUID) -> list[dict[str, Any]]:
        table = metadata.tables["batch"]
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    sa.select(*table.c)
                    .where(table.c.workspace_id == workspace_id)
                    .order_by(table.c.updated_at.desc(), table.c.label)
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    @requires("files.upload")
    def update_batch(
        self,
        actor: ActorContext,
        batch_id: UUID,
        *,
        expected_version: int,
        label: str | None,
        period_start: date | None,
        period_end: date | None,
    ) -> dict[str, Any] | None:
        table = metadata.tables["batch"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.update(table)
                    .where(
                        table.c.workspace_id == actor.workspace_id,
                        table.c.id == batch_id,
                        table.c.version == expected_version,
                    )
                    .values(
                        label=label.strip() if label and label.strip() else None,
                        label_is_suggested=False,
                        period_start=period_start,
                        period_end=period_end,
                        updated_at=sa.func.now(),
                        version=table.c.version + 1,
                    )
                    .returning(*table.c)
                )
                .mappings()
                .one_or_none()
            )
            if row is not None:
                return dict(row)
            exists = connection.scalar(
                sa.select(table.c.id).where(
                    table.c.workspace_id == actor.workspace_id,
                    table.c.id == batch_id,
                )
            )
            if exists is None:
                return None
            raise BatchVersionConflict

    @requires("files.upload")
    def receive_file(
        self,
        actor: ActorContext,
        *,
        batch_id: UUID,
        zone: UploadZone,
        display_name: str,
        size_bytes: int,
        sha256: str,
        file_format: FileFormat,
        stream: BinaryIO,
    ) -> UploadReceipt:
        limits = self.limits(actor.workspace_id)
        table = metadata.tables["source_file"]
        key = f"{actor.workspace_id}/sha256/{sha256}"

        with self.engine.begin() as connection:
            batch_exists = connection.scalar(
                sa.select(metadata.tables["batch"].c.id).where(
                    metadata.tables["batch"].c.workspace_id == actor.workspace_id,
                    metadata.tables["batch"].c.id == batch_id,
                )
            )
            if batch_exists is None:
                raise IngestionFailure("BATCH_NOT_FOUND", "The selected batch was not found.")
            existing = (
                connection.execute(
                    sa.select(table)
                    .where(table.c.workspace_id == actor.workspace_id, table.c.sha256 == sha256)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None and existing["status"] != "removed":
                return self._duplicate_receipt(connection, actor, existing, display_name)

            oversized = size_bytes > limits.max_file_bytes
            if not oversized:
                try:
                    self.object_store.put_fileobj(key, stream, _media_type(file_format))
                except (BotoCoreError, OSError) as exc:
                    raise ObjectStoreUnavailable from exc

            if existing is not None:
                values = {
                    "batch_id": batch_id,
                    "parent_file_id": None,
                    "display_name": safe_display_name(display_name),
                    "archive_path": None,
                    "zone": zone.value,
                    "zone_uploaded": zone.value,
                    "format": file_format.value,
                    "sniffed_type": None,
                    "mismatch_state": "none",
                    "status": "failed" if oversized else "staged",
                    "error_code": "FILE_TOO_LARGE" if oversized else None,
                    "error_message": "The file exceeds the configured upload limit."
                    if oversized
                    else None,
                    "attempt_count": 0,
                    "size_bytes": size_bytes,
                    "object_key": key,
                    "docs_found": 0,
                    "docs_new": 0,
                    "docs_updated": 0,
                    "docs_skipped": 0,
                    "uploaded_by": actor.actor_id,
                    "queued_at": None,
                    "parse_started_at": None,
                    "parse_finished_at": None,
                    "removed_at": None,
                    "removed_by": None,
                    "object_purged_at": None,
                    "version": table.c.version + 1,
                }
                connection.execute(
                    sa.update(table).where(table.c.id == existing["id"]).values(**values)
                )
                file_id = cast(UUID, existing["id"])
                reactivated = True
            else:
                file_id = new_id()
                try:
                    # The unique (workspace, sha256) index is the guard against two
                    # simultaneous uploads of the same bytes: the later one is a duplicate.
                    with connection.begin_nested():
                        self._insert_received_file(
                            connection,
                            actor,
                            file_id=file_id,
                            batch_id=batch_id,
                            sha256=sha256,
                            display_name=display_name,
                            zone=zone,
                            file_format=file_format,
                            oversized=oversized,
                            size_bytes=size_bytes,
                            key=key,
                        )
                except sa.exc.IntegrityError:
                    raced = (
                        connection.execute(
                            sa.select(table).where(
                                table.c.workspace_id == actor.workspace_id,
                                table.c.sha256 == sha256,
                            )
                        )
                        .mappings()
                        .one()
                    )
                    return self._duplicate_receipt(connection, actor, raced, display_name)
                reactivated = False

            _event(
                connection,
                workspace_id=actor.workspace_id,
                file_id=file_id,
                stage="received",
                outcome="error" if oversized else "ok",
                error_code="FILE_TOO_LARGE" if oversized else None,
                detail={"size_bytes": size_bytes, "sha256": sha256},
            )
            if oversized:
                connection.execute(
                    sa.insert(metadata.tables["document_warning"]).values(
                        id=new_id(),
                        workspace_id=actor.workspace_id,
                        source_file_id=file_id,
                        code="FILE_TOO_LARGE",
                        level="error",
                        message="The file exceeds the configured upload limit.",
                        detail={"max_bytes": limits.max_file_bytes, "size_bytes": size_bytes},
                    )
                )

        return UploadReceipt(
            file_id=file_id,
            sha256=sha256,
            size_bytes=size_bytes,
            format=file_format.value,
            status="failed" if oversized else "staged",
            reactivated=reactivated,
        )

    def _duplicate_receipt(
        self,
        connection: Connection,
        actor: ActorContext,
        existing: Mapping[str, Any],
        display_name: str,
    ) -> UploadReceipt:
        """The same bytes are already an active upload: nothing is stored or queued."""

        table = metadata.tables["source_file"]
        connection.execute(
            sa.update(table)
            .where(table.c.id == existing["id"])
            .values(reupload_skips=table.c.reupload_skips + 1, version=table.c.version + 1)
        )
        write_audit_event(
            connection,
            actor,
            action="file.duplicate_skipped",
            target_type="file",
            target_id=cast(UUID, existing["id"]),
            after_state={
                "reason": "already_uploaded",
                "uploaded_as": safe_display_name(display_name),
            },
        )
        return UploadReceipt(
            file_id=cast(UUID, existing["id"]),
            sha256=str(existing["sha256"]),
            size_bytes=int(existing["size_bytes"]),
            format=str(existing["format"]),
            status=str(existing["status"]),
            duplicate=True,
            original_display_name=str(existing["display_name"]),
            original_batch_id=cast(UUID | None, existing["batch_id"]),
        )

    @staticmethod
    def _insert_received_file(
        connection: Connection,
        actor: ActorContext,
        *,
        file_id: UUID,
        batch_id: UUID,
        sha256: str,
        display_name: str,
        zone: UploadZone,
        file_format: FileFormat,
        oversized: bool,
        size_bytes: int,
        key: str,
    ) -> None:
        table = metadata.tables["source_file"]
        connection.execute(
            sa.insert(table).values(
                id=file_id,
                workspace_id=actor.workspace_id,
                batch_id=batch_id,
                parent_file_id=None,
                sha256=sha256,
                display_name=safe_display_name(display_name),
                archive_path=None,
                zone=zone.value,
                zone_uploaded=zone.value,
                format=file_format.value,
                sniffed_type=None,
                mismatch_state="none",
                status="failed" if oversized else "staged",
                error_code="FILE_TOO_LARGE" if oversized else None,
                error_message="The file exceeds the configured upload limit."
                if oversized
                else None,
                attempt_count=0,
                size_bytes=size_bytes,
                object_key=key,
                uploaded_by=actor.actor_id,
            )
        )

    @requires("files.upload")
    def queue_files(
        self,
        actor: ActorContext,
        *,
        batch_id: UUID,
        retry_file_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        table = metadata.tables["source_file"]
        with self.engine.begin() as connection:
            query = sa.select(table).where(
                table.c.workspace_id == actor.workspace_id,
                table.c.batch_id == batch_id,
                table.c.parent_file_id.is_(None),
                table.c.removed_at.is_(None),
            )
            if retry_file_id is not None:
                query = query.where(table.c.id == retry_file_id)
            else:
                query = query.where(table.c.status == "staged")
            files = connection.execute(query.with_for_update()).mappings().all()
            queued: list[dict[str, Any]] = []
            for file_row in files:
                if file_row["mismatch_state"] == "suggested":
                    continue
                file_id = cast(UUID, file_row["id"])
                connection.execute(
                    sa.update(table)
                    .where(table.c.id == file_id)
                    .values(
                        status="queued",
                        error_code=None,
                        error_message=None,
                        queued_at=sa.func.now(),
                        parse_finished_at=None,
                        version=table.c.version + 1,
                    )
                )
                job_id = self.queue.enqueue("ingest_file", file_id=file_id, connection=connection)
                _event(
                    connection,
                    workspace_id=actor.workspace_id,
                    file_id=file_id,
                    stage="sniffed",
                    outcome="ok",
                    job_id=job_id,
                    detail={"format": str(file_row["format"]), "queued": True},
                )
                queued.append({"file_id": file_id, "job_id": job_id})
            return queued

    @requires("files.upload")
    def retry_file(self, actor: ActorContext, file_id: UUID) -> dict[str, Any]:
        table = metadata.tables["source_file"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(table)
                    .where(
                        table.c.workspace_id == actor.workspace_id,
                        table.c.id == file_id,
                        table.c.removed_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise IngestionFailure("FILE_NOT_FOUND", "The file was not found.")
            retryable_pdf_without_text = (
                row["status"] == "parsed"
                and row["format"] == FileFormat.PDF.value
                and file_id in self._pdf_files_without_extracted_text(connection, [file_id])
            )
            parser_refresh_required = _parser_refresh_required(row)
            if (
                row["status"] not in {"failed", "partial"}
                and not retryable_pdf_without_text
                and not parser_refresh_required
            ):
                raise IngestionFailure(
                    "FILE_NOT_RETRYABLE",
                    "Only failed, partial, textless scanned PDF, or outdated parser files "
                    "can be retried.",
                )
            connection.execute(
                sa.update(table)
                .where(table.c.id == file_id)
                .values(
                    status="queued",
                    error_code=None,
                    error_message=None,
                    queued_at=sa.func.now(),
                    parse_finished_at=None,
                    version=table.c.version + 1,
                )
            )
            job_id = self.queue.enqueue("ingest_file", file_id=file_id, connection=connection)
            _event(
                connection,
                workspace_id=actor.workspace_id,
                file_id=file_id,
                stage="sniffed",
                outcome="ok",
                job_id=job_id,
                detail={"manual_retry": True, "parser_refresh": parser_refresh_required},
            )
        return {"file_id": file_id, "job_id": job_id, "status": "queued"}

    @requires("data.view")
    def list_duplicate_holds(
        self,
        workspace_id: UUID,
        *,
        file_id: UUID | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        """Uploads not ingested because they match an active invoice (PD-0b-4)."""

        hold = metadata.tables["duplicate_hold"]
        document = metadata.tables["document"]
        version = metadata.tables["document_version"]
        source = metadata.tables["source_file"]
        original_file = source.alias("original_file")
        query = (
            sa.select(
                hold.c.id,
                hold.c.source_file_id,
                source.c.display_name.label("file_name"),
                hold.c.kind,
                hold.c.reason,
                hold.c.doc_no,
                hold.c.state,
                hold.c.created_at,
                hold.c.decided_at,
                hold.c.version,
                hold.c.duplicate_of_document_id,
                document.c.doc_no.label("original_doc_no"),
                document.c.withdrawn_at.is_not(None).label("original_withdrawn"),
                original_file.c.id.label("original_file_id"),
                original_file.c.display_name.label("original_file_name"),
                original_file.c.batch_id.label("original_batch_id"),
            )
            .select_from(
                hold.join(source, source.c.id == hold.c.source_file_id)
                .join(document, document.c.id == hold.c.duplicate_of_document_id)
                .outerjoin(version, version.c.id == document.c.current_version_id)
                .outerjoin(original_file, original_file.c.id == version.c.source_file_id)
            )
            .where(hold.c.workspace_id == workspace_id)
            .order_by(hold.c.created_at.desc(), hold.c.id)
        )
        if file_id is not None:
            query = query.where(hold.c.source_file_id == file_id)
        if state is not None:
            query = query.where(hold.c.state == state)
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(query.limit(500)).mappings()]

    @requires("files.upload")
    def decide_duplicate_hold(
        self,
        actor: ActorContext,
        hold_id: UUID,
        *,
        expected_version: int,
        decision: str,
    ) -> dict[str, Any] | None:
        """Discard, Replace with this file (duplicate) or Keep as new (possible), audited."""

        hold = metadata.tables["duplicate_hold"]
        source = metadata.tables["source_file"]
        states = {"discard": "discarded", "replace": "replaced", "keep": "kept"}
        if decision not in states:
            raise IngestionFailure("DUPLICATE_DECISION_INVALID", "Choose discard, replace or keep.")
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(hold)
                    .where(hold.c.workspace_id == actor.workspace_id, hold.c.id == hold_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            if int(row["version"]) != expected_version:
                raise BatchVersionConflict("The duplicate decision changed after it was loaded.")
            if row["state"] != "open":
                raise IngestionFailure(
                    "DUPLICATE_ALREADY_DECIDED", "This duplicate was already decided."
                )
            if decision == "replace" and row["kind"] != "duplicate":
                raise IngestionFailure(
                    "DUPLICATE_DECISION_INVALID",
                    "A possible duplicate can be discarded or kept as new, "
                    "not replace the original.",
                )
            if decision == "keep" and row["kind"] != "possible":
                raise IngestionFailure(
                    "DUPLICATE_DECISION_INVALID",
                    "A duplicate can be discarded or replace the original, "
                    "not be kept as a second one.",
                )
            connection.execute(
                sa.update(hold)
                .where(hold.c.id == hold_id)
                .values(
                    state=states[decision],
                    decided_by=actor.actor_id,
                    decided_at=sa.func.now(),
                    version=hold.c.version + 1,
                )
            )
            write_audit_event(
                connection,
                actor,
                action="document.duplicate_decide",
                target_type="file",
                target_id=cast(UUID, row["source_file_id"]),
                before_state={"state": "open", "kind": row["kind"], "reason": row["reason"]},
                after_state={
                    "state": states[decision],
                    "hold_id": str(hold_id),
                    "duplicate_of_document_id": str(row["duplicate_of_document_id"]),
                },
            )
            if decision != "discard":
                # Read the file again: the decided document is ingested as a new version of
                # the original (replace) or as a new document (keep); the rest is unchanged.
                connection.execute(
                    sa.update(source)
                    .where(
                        source.c.workspace_id == actor.workspace_id,
                        source.c.id == row["source_file_id"],
                        source.c.removed_at.is_(None),
                    )
                    .values(
                        status="queued",
                        error_code=None,
                        error_message=None,
                        queued_at=sa.func.now(),
                        parse_finished_at=None,
                        version=source.c.version + 1,
                    )
                )
                self.queue.enqueue(
                    "ingest_file", file_id=cast(UUID, row["source_file_id"]), connection=connection
                )
        holds = self.list_duplicate_holds(actor.workspace_id, file_id=row["source_file_id"])
        return next(item for item in holds if item["id"] == hold_id)

    @requires("files.upload")
    def save_csv_mapping(
        self,
        actor: ActorContext,
        file_id: UUID,
        *,
        column_mapping: Mapping[str, str],
    ) -> dict[str, Any]:
        """Save a reviewed CSV layout and requeue its failed parse."""

        if len(column_mapping) < 3:
            raise IngestionFailure(
                "CSV_MAPPING_INCOMPLETE", "Map at least three columns before retrying."
            )
        if any(
            not isinstance(label, str)
            or not label.strip()
            or not isinstance(field, str)
            or field not in _CSV_MAPPING_FIELDS
            for label, field in column_mapping.items()
        ):
            raise IngestionFailure(
                "CSV_MAPPING_INVALID", "The column mapping contains an unsupported field."
            )

        source = metadata.tables["source_file"]
        profile = metadata.tables["layout_profile"]
        mapping_body = {"column_mapping": dict(column_mapping)}
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(source)
                    .where(
                        source.c.workspace_id == actor.workspace_id,
                        source.c.id == file_id,
                        source.c.removed_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise IngestionFailure("FILE_NOT_FOUND", "The file was not found.")
            if row["format"] != FileFormat.CSV.value or row["error_code"] != "CSV_MAPPING_REQUIRED":
                raise IngestionFailure(
                    "CSV_MAPPING_NOT_REQUIRED",
                    "A saved column map can only retry a CSV that needs a mapping.",
                )
            signature = row["layout_signature"]
            if not signature:
                raise IngestionFailure(
                    "CSV_LAYOUT_UNKNOWN", "The failed CSV has no layout signature to save."
                )

            statement = (
                pg_insert(profile)
                .values(
                    id=new_id(),
                    workspace_id=actor.workspace_id,
                    signature=signature,
                    kind="csv_mapping",
                    vendor_id=None,
                    body=mapping_body,
                    created_by=actor.actor_id,
                )
                .on_conflict_do_update(
                    constraint="layout_profile_workspace_id_signature_kind_vendor_id_key",
                    set_={"body": mapping_body, "updated_at": sa.func.now()},
                )
            )
            connection.execute(statement)
            connection.execute(
                sa.update(source)
                .where(source.c.workspace_id == actor.workspace_id, source.c.id == file_id)
                .values(
                    status="queued",
                    error_code=None,
                    error_message=None,
                    queued_at=sa.func.now(),
                    parse_finished_at=None,
                    version=source.c.version + 1,
                )
            )
            job_id = self.queue.enqueue("ingest_file", file_id=file_id, connection=connection)
            write_audit_event(
                connection,
                actor,
                action="file.csv_mapping.save",
                target_type="file",
                target_id=file_id,
                before_state={"layout_signature": signature},
                after_state={"mapped_columns": len(column_mapping), "retry_queued": True},
            )
            _event(
                connection,
                workspace_id=actor.workspace_id,
                file_id=file_id,
                stage="sniffed",
                outcome="ok",
                job_id=job_id,
                detail={"csv_mapping_saved": True, "mapped_columns": len(column_mapping)},
            )
        return {"file_id": file_id, "job_id": job_id, "status": "queued"}

    @requires("files.upload")
    def resolve_type_mismatch(
        self,
        actor: ActorContext,
        file_id: UUID,
        *,
        accept_suggested: bool,
        expected_version: int,
    ) -> dict[str, Any]:
        table = metadata.tables["source_file"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(table)
                    .where(
                        table.c.workspace_id == actor.workspace_id,
                        table.c.id == file_id,
                        table.c.removed_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise IngestionFailure("FILE_NOT_FOUND", "The file was not found.")
            if row["version"] != expected_version:
                raise BatchVersionConflict
            if row["mismatch_state"] != "suggested" or row["sniffed_type"] is None:
                raise IngestionFailure("TYPE_MISMATCH_NOT_PENDING", "No suggested type is pending.")
            new_zone = row["sniffed_type"] if accept_suggested else row["zone_uploaded"]
            state = "accepted" if accept_suggested else "kept"
            updated = (
                connection.execute(
                    sa.update(table)
                    .where(table.c.id == file_id)
                    .values(
                        zone=new_zone,
                        mismatch_state=state,
                        status="staged",
                        error_code=None,
                        error_message=None,
                        version=table.c.version + 1,
                    )
                    .returning(*table.c)
                )
                .mappings()
                .one()
            )
            warning = metadata.tables["document_warning"]
            connection.execute(
                sa.update(warning)
                .where(
                    warning.c.workspace_id == actor.workspace_id,
                    warning.c.source_file_id == file_id,
                    warning.c.code == "TYPE_MISMATCH",
                    warning.c.resolved_at.is_(None),
                )
                .values(resolved_at=sa.func.now(), resolved_by=actor.actor_id)
            )
            write_audit_event(
                connection,
                actor,
                action="file.type_mismatch.resolve",
                target_type="file",
                target_id=file_id,
                before_state={"zone": str(row["zone"]), "mismatch_state": "suggested"},
                after_state={"zone": new_zone, "mismatch_state": state},
            )
            _event(
                connection,
                workspace_id=actor.workspace_id,
                file_id=file_id,
                stage="sniffed",
                outcome="ok",
                detail={"mismatch_resolution": state, "effective_zone": new_zone},
            )
        return dict(updated)

    @requires("files.upload")
    def soft_delete(self, actor: ActorContext, file_id: UUID) -> dict[str, Any]:
        source = metadata.tables["source_file"]
        document = metadata.tables["document"]
        document_version = metadata.tables["document_version"]
        with self.engine.begin() as connection:
            file_row = (
                connection.execute(
                    sa.select(source)
                    .where(
                        source.c.workspace_id == actor.workspace_id,
                        source.c.id == file_id,
                        source.c.removed_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if file_row is None:
                raise IngestionFailure("FILE_NOT_FOUND", "The file was not found.")

            affected = (
                connection.execute(
                    sa.select(document)
                    .where(
                        document.c.workspace_id == actor.workspace_id,
                        document.c.id.in_(
                            sa.select(document_version.c.document_id).where(
                                document_version.c.workspace_id == actor.workspace_id,
                                document_version.c.source_file_id == file_id,
                            )
                        ),
                    )
                    .with_for_update()
                )
                .mappings()
                .all()
            )
            connection.execute(
                sa.update(source)
                .where(source.c.id == file_id, source.c.workspace_id == actor.workspace_id)
                .values(
                    status="removed",
                    removed_at=sa.func.now(),
                    removed_by=actor.actor_id,
                    version=source.c.version + 1,
                )
            )
            self._settle_documents(
                connection,
                workspace_id=actor.workspace_id,
                actor_id=actor.actor_id,
                file_id=file_id,
                rows=affected,
                exclude_file=False,
                reason="source file removed",
            )
            write_audit_event(
                connection,
                actor,
                action="file.remove",
                target_type="file",
                target_id=file_id,
                before_state={"status": str(file_row["status"]), "version": file_row["version"]},
                after_state={"status": "removed", "withdrawn_documents": len(affected)},
            )
            _event(
                connection,
                workspace_id=actor.workspace_id,
                file_id=file_id,
                stage="gated",
                outcome="ok",
                detail={"soft_deleted": True, "withdrawn_documents": len(affected)},
            )
        return {"file_id": file_id, "status": "removed", "withdrawn_documents": len(affected)}

    def _settle_documents(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        actor_id: UUID | None,
        file_id: UUID,
        rows: Sequence[Mapping[str, Any]],
        exclude_file: bool,
        reason: str,
    ) -> None:
        """Point each document at its newest live version, or withdraw it.

        A version is live while its file is not removed; with ``exclude_file`` the
        versions read from ``file_id`` no longer count either (a page split undone).
        """

        source = metadata.tables["source_file"]
        document = metadata.tables["document"]
        document_version = metadata.tables["document_version"]
        dirty_scopes: set[tuple[str, UUID]] = set()
        for row in rows:
            doc_id = cast(UUID, row["id"])
            conditions = [
                document_version.c.workspace_id == workspace_id,
                document_version.c.document_id == doc_id,
                source.c.removed_at.is_(None),
            ]
            if exclude_file:
                conditions.append(document_version.c.source_file_id != file_id)
            live_version = (
                connection.execute(
                    sa.select(document_version)
                    .select_from(
                        document_version.join(
                            source,
                            sa.and_(
                                source.c.workspace_id == document_version.c.workspace_id,
                                source.c.id == document_version.c.source_file_id,
                            ),
                        )
                    )
                    .where(*conditions)
                    .order_by(
                        document_version.c.revision_rank.desc(), document_version.c.seq.desc()
                    )
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if live_version is None:
                connection.execute(
                    sa.update(document)
                    .where(document.c.id == doc_id, document.c.workspace_id == workspace_id)
                    .values(
                        current_version_id=None,
                        withdrawn_at=sa.func.now(),
                        withdrawn_by=actor_id,
                        updated_at=sa.func.now(),
                        input_seq=document.c.input_seq + (1 if row["doc_type"] == "po" else 0),
                        version=document.c.version + 1,
                    )
                )
            else:
                connection.execute(
                    sa.update(document)
                    .where(document.c.id == doc_id, document.c.workspace_id == workspace_id)
                    .values(
                        current_version_id=live_version["id"],
                        doc_no=live_version["doc_no"],
                        canonical_doc_no=live_version["canonical_doc_no"],
                        doc_date=live_version["doc_date"],
                        grand_total=live_version["grand_total"],
                        needs_verification=live_version["needs_verification"],
                        withdrawn_at=None,
                        withdrawn_by=None,
                        updated_at=sa.func.now(),
                        input_seq=document.c.input_seq + (1 if row["doc_type"] == "po" else 0),
                        version=document.c.version + 1,
                    )
                )
            if row["doc_type"] == "po":
                dirty_scopes.add(("po", doc_id))
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                document_id=doc_id,
                document_version_id=cast(UUID | None, live_version["id"])
                if live_version is not None
                else None,
                stage="gated",
                outcome="warn",
                detail={
                    "source_removed" if not exclude_file else "page_split_undone": True,
                    "document_withdrawn": live_version is None,
                    "current_version_id": str(live_version["id"])
                    if live_version is not None
                    else None,
                },
            )
        for scope_kind, scope_id in dirty_scopes:
            self._mark_dirty_scope(
                connection,
                workspace_id=workspace_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                reason=reason,
            )
        if any(row["doc_type"] != "po" for row in rows):
            # A withdrawn invoice or GRN changes the ledgers of the POs it was linked to
            # without marking a scope: queue the drain, which runs before any mapping job
            # queued later, so no ledger row is read stale (PD-0b-3).
            enqueue_pending = getattr(self.queue, "enqueue_pending_recomputes", None)
            if callable(enqueue_pending):
                enqueue_pending(workspace_id=workspace_id, connection=connection)

    @requires("files.upload")
    def ingest_file(self, file_id: UUID, *, job_id: int | None = None) -> None:
        source = metadata.tables["source_file"]
        event_table = metadata.tables["processing_event"]
        with self.engine.connect() as connection:
            file_row = (
                connection.execute(sa.select(source).where(source.c.id == file_id))
                .mappings()
                .one_or_none()
            )
        if file_row is None or file_row["status"] != "queued" or file_row["removed_at"] is not None:
            return
        if file_row["format"] == FileFormat.ZIP.value:
            if job_id is not None:
                with self.engine.connect() as connection:
                    completed = connection.scalar(
                        sa.select(event_table.c.id)
                        .where(
                            event_table.c.source_file_id == file_id,
                            event_table.c.job_id == job_id,
                            event_table.c.stage == "parsed",
                            event_table.c.detail["archive_scan_complete"].as_boolean().is_(True),
                        )
                        .limit(1)
                    )
                if completed is not None:
                    return
            self._ingest_archive(file_row, job_id=job_id)
            return

        content = self._read_object(cast(str, file_row["object_key"]))
        detected_format = sniff_format(content[:4096])
        if detected_format.value != str(file_row["format"]):
            self._fail_file(
                file_id,
                "CONTENT_FORMAT_MISMATCH",
                "The stored content no longer matches its recorded format.",
                job_id=job_id,
            )
            return
        sniffed_type = sniff_document_type(content, detected_format)
        workspace_id = cast(UUID, file_row["workspace_id"])

        with self.engine.begin() as connection:
            locked = (
                connection.execute(
                    sa.select(source)
                    .where(source.c.workspace_id == workspace_id, source.c.id == file_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if locked is None or locked["status"] != "queued":
                return
            if job_id is not None:
                completed = connection.scalar(
                    sa.select(event_table.c.id)
                    .where(
                        event_table.c.source_file_id == file_id,
                        event_table.c.job_id == job_id,
                        event_table.c.stage == "sniffed",
                        event_table.c.detail["ingest_complete"].as_boolean().is_(True),
                    )
                    .limit(1)
                )
                if completed is not None:
                    return
            mismatch = sniffed_type is not None and sniffed_type.value != locked["zone"]
            connection.execute(
                sa.update(source)
                .where(source.c.id == file_id, source.c.workspace_id == workspace_id)
                .values(
                    sniffed_type=sniffed_type.value if sniffed_type else None,
                    mismatch_state="suggested" if mismatch else locked["mismatch_state"],
                    status="staged" if mismatch else "queued",
                    error_code="TYPE_MISMATCH" if mismatch else None,
                    error_message=(
                        f"Content looks like {sniffed_type.value}; confirm the suggested type "
                        "or keep the upload zone."
                        if mismatch
                        else None
                    ),
                    version=source.c.version + (1 if mismatch else 0),
                )
            )
            if mismatch:
                connection.execute(
                    sa.insert(metadata.tables["document_warning"]).values(
                        id=new_id(),
                        workspace_id=workspace_id,
                        source_file_id=file_id,
                        code="TYPE_MISMATCH",
                        level="warning",
                        message=(
                            f"The uploaded zone is {locked['zone']}, while content suggests "
                            f"{sniffed_type.value}. Confirm or keep the original zone."
                        ),
                        detail={
                            "zone_uploaded": locked["zone"],
                            "suggested_type": sniffed_type.value,
                        },
                    )
                )
                _event(
                    connection,
                    workspace_id=workspace_id,
                    file_id=file_id,
                    stage="sniffed",
                    outcome="warn",
                    warning_codes=("TYPE_MISMATCH",),
                    job_id=job_id,
                    detail={
                        "zone_uploaded": locked["zone"],
                        "suggested_type": sniffed_type.value,
                        "ingest_complete": True,
                    },
                )
                return
            parse_job_id = self.queue.enqueue("parse_file", file_id=file_id, connection=connection)
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                stage="sniffed",
                outcome="ok",
                job_id=job_id,
                detail={
                    "format": detected_format.value,
                    "sniffed_type": sniffed_type.value if sniffed_type else None,
                    "ingest_complete": True,
                },
            )
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                stage="sniffed",
                outcome="ok",
                job_id=job_id,
                detail={"parse_job_queued": True, "parse_job_id": parse_job_id},
            )

    def _ingest_archive(self, parent: Mapping[str, Any], *, job_id: int | None) -> None:
        workspace_id = cast(UUID, parent["workspace_id"])
        parent_id = cast(UUID, parent["id"])
        with tempfile.TemporaryFile(mode="w+b") as archive_stream:
            object_read = self.object_store.get(cast(str, parent["object_key"]))
            try:
                shutil.copyfileobj(object_read.stream, archive_stream, length=_READ_CHUNK_BYTES)
            finally:
                close = getattr(object_read.stream, "close", None)
                if callable(close):
                    close()
            archive_stream.seek(0)
            try:
                archive = zipfile.ZipFile(archive_stream)
            except (zipfile.BadZipFile, OSError):
                self._fail_file(
                    parent_id, "INVALID_ZIP", "The archive is not a valid ZIP file.", job_id=job_id
                )
                return
            with archive:
                limits = self.limits(workspace_id)
                infos = archive.infolist()
                total_uncompressed = sum(info.file_size for info in infos if not info.is_dir())
                total_compressed = sum(info.compress_size for info in infos if not info.is_dir())
                invalid = self._archive_validation_error(
                    infos, total_uncompressed, total_compressed, limits
                )
                if invalid is not None:
                    self._fail_file(parent_id, invalid[0], invalid[1], job_id=job_id)
                    return

                children: list[tuple[UUID, bool]] = []
                expanded_total = 0
                for info in infos:
                    if info.is_dir() or _is_skipped_archive_name(info.filename):
                        continue
                    entry_path = _validated_archive_path(info.filename)
                    if entry_path is None:
                        self._fail_file(
                            parent_id,
                            "ZIP_PATH_TRAVERSAL",
                            "The ZIP contains an absolute or parent-traversal path.",
                            job_id=job_id,
                        )
                        return
                    try:
                        with tempfile.SpooledTemporaryFile(
                            max_size=1024 * 1024, mode="w+b"
                        ) as child_stream:
                            digest = hashlib.sha256()
                            size = 0
                            with archive.open(info, "r") as entry:
                                while chunk := entry.read(_READ_CHUNK_BYTES):
                                    size += len(chunk)
                                    if size > limits.max_file_bytes or size > info.file_size:
                                        self._fail_file(
                                            parent_id,
                                            "ZIP_ENTRY_SIZE_MISMATCH",
                                            "An archive entry exceeded its declared or configured "
                                            "size.",
                                            job_id=job_id,
                                        )
                                        return
                                    digest.update(chunk)
                                    child_stream.write(chunk)
                            if size != info.file_size:
                                self._fail_file(
                                    parent_id,
                                    "ZIP_ENTRY_SIZE_MISMATCH",
                                    "An archive entry did not match its declared "
                                    "uncompressed size.",
                                    job_id=job_id,
                                )
                                return
                            expanded_total += size
                            if expanded_total > limits.max_zip_uncompressed_bytes:
                                self._fail_file(
                                    parent_id,
                                    "ZIP_UNCOMPRESSED_LIMIT",
                                    "The archive exceeded the total expanded size limit "
                                    "while reading.",
                                    job_id=job_id,
                                )
                                return
                            child_stream.seek(0)
                            content_prefix = child_stream.read(4096)
                            child_stream.seek(0)
                            child_format = sniff_format(content_prefix)
                            child_hash = digest.hexdigest()
                            nested = child_format is FileFormat.ZIP
                            mismatch_type = None
                            if not nested:
                                child_content = child_stream.read()
                                child_stream.seek(0)
                                mismatch_type = sniff_document_type(child_content, child_format)
                            child_id, should_queue = self._record_archive_child(
                                parent=parent,
                                path=entry_path,
                                file_format=child_format,
                                size_bytes=size,
                                sha256=child_hash,
                                stream=child_stream,
                                sniffed_type=mismatch_type,
                                nested=nested,
                            )
                    except (
                        zipfile.BadZipFile,
                        RuntimeError,
                        OSError,
                        EOFError,
                        NotImplementedError,
                    ):
                        self._fail_file(
                            parent_id,
                            "ZIP_CORRUPT_ENTRY",
                            "An archive entry could not be read safely.",
                            job_id=job_id,
                        )
                        return
                    children.append((child_id, should_queue))

                self._finish_archive(parent, children, job_id=job_id)

    @staticmethod
    def _archive_validation_error(
        infos: list[zipfile.ZipInfo],
        total_uncompressed: int,
        total_compressed: int,
        limits: IngestionLimits,
    ) -> tuple[str, str] | None:
        if len(infos) > limits.zip_max_entries:
            return "ZIP_ENTRY_LIMIT", "The archive exceeds the configured entry count."
        if total_uncompressed > limits.max_zip_uncompressed_bytes:
            return "ZIP_UNCOMPRESSED_LIMIT", "The archive exceeds the total expanded size limit."
        if total_uncompressed and (
            total_compressed == 0 or total_uncompressed > total_compressed * limits.zip_max_ratio
        ):
            return "ZIP_COMPRESSION_RATIO", "The archive exceeds the configured compression ratio."
        if any(
            not info.is_dir() and _validated_archive_path(info.filename) is None for info in infos
        ):
            return (
                "ZIP_PATH_TRAVERSAL",
                "The archive contains an absolute or parent-traversal path.",
            )
        if any(info.flag_bits & 0x1 for info in infos):
            return "ENCRYPTED_ARCHIVE", "Password-protected ZIP archives are not supported."
        return None

    def _record_archive_child(
        self,
        *,
        parent: Mapping[str, Any],
        path: str,
        file_format: FileFormat,
        size_bytes: int,
        sha256: str,
        stream: BinaryIO,
        sniffed_type: DocType | None,
        nested: bool,
    ) -> tuple[UUID, bool]:
        workspace_id = cast(UUID, parent["workspace_id"])
        source = metadata.tables["source_file"]
        object_key = f"{workspace_id}/sha256/{sha256}"
        mismatch = sniffed_type is not None and sniffed_type.value != parent["zone"]
        failed_code = "NESTED_ARCHIVE_REJECTED" if nested else None
        if not nested:
            try:
                self.object_store.put_fileobj(object_key, stream, _media_type(file_format))
            except (BotoCoreError, OSError) as exc:
                raise ObjectStoreUnavailable from exc
        with self.engine.begin() as connection:
            existing = (
                connection.execute(
                    sa.select(source)
                    .where(source.c.workspace_id == workspace_id, source.c.sha256 == sha256)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None and existing["removed_at"] is None:
                connection.execute(
                    sa.update(source)
                    .where(source.c.id == existing["id"])
                    .values(
                        reupload_skips=source.c.reupload_skips + 1, version=source.c.version + 1
                    )
                )
                return cast(UUID, existing["id"]), False
            child_failed = nested
            mismatch_state = "suggested" if mismatch else "none"
            child_status = "failed" if child_failed else "staged"
            child_values = {
                "batch_id": parent["batch_id"],
                "parent_file_id": parent["id"],
                "display_name": PurePosixPath(path).name or "archive-entry",
                "archive_path": path,
                "zone": parent["zone"],
                "zone_uploaded": parent["zone_uploaded"],
                "format": file_format.value,
                "sniffed_type": sniffed_type.value if sniffed_type else None,
                "mismatch_state": mismatch_state,
                "status": child_status,
                "error_code": failed_code,
                "error_message": "Nested archives are not accepted." if child_failed else None,
                "size_bytes": size_bytes,
                "object_key": object_key,
                "uploaded_by": parent["uploaded_by"],
                "attempt_count": 0,
                "docs_found": 0,
                "docs_new": 0,
                "docs_updated": 0,
                "docs_skipped": 0,
                "queued_at": None,
                "parse_started_at": None,
                "parse_finished_at": None,
                "removed_at": None,
                "removed_by": None,
                "object_purged_at": None,
            }
            if existing is not None:
                child_id = cast(UUID, existing["id"])
                connection.execute(
                    sa.update(source)
                    .where(source.c.id == child_id)
                    .values(**child_values, version=source.c.version + 1)
                )
            else:
                child_id = new_id()
                connection.execute(
                    sa.insert(source).values(
                        id=child_id,
                        workspace_id=workspace_id,
                        sha256=sha256,
                        **child_values,
                    )
                )
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=child_id,
                stage="received",
                outcome="error" if child_failed else "ok",
                error_code=failed_code,
                detail={
                    "parent_file_id": str(parent["id"]),
                    "archive_path": path,
                    "sha256": sha256,
                },
            )
            if mismatch:
                connection.execute(
                    sa.insert(metadata.tables["document_warning"]).values(
                        id=new_id(),
                        workspace_id=workspace_id,
                        source_file_id=child_id,
                        code="TYPE_MISMATCH",
                        level="warning",
                        message=(
                            f"The upload zone is {parent['zone']}; content suggests "
                            f"{sniffed_type.value}."
                        ),
                        detail={
                            "zone_uploaded": parent["zone"],
                            "suggested_type": sniffed_type.value,
                        },
                    )
                )
            if child_failed:
                return child_id, False
            if mismatch:
                return child_id, False
            connection.execute(
                sa.update(source)
                .where(source.c.id == child_id)
                .values(status="queued", queued_at=sa.func.now())
            )
            queue_id = self.queue.enqueue("parse_file", file_id=child_id, connection=connection)
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=child_id,
                stage="sniffed",
                outcome="ok",
                job_id=queue_id,
                detail={"format": file_format.value, "zip_child": True},
            )
            return child_id, True

    def _finish_archive(
        self,
        parent: Mapping[str, Any],
        children: list[tuple[UUID, bool]],
        *,
        job_id: int | None,
    ) -> None:
        workspace_id = cast(UUID, parent["workspace_id"])
        file_id = cast(UUID, parent["id"])
        source = metadata.tables["source_file"]
        child_ids = [child_id for child_id, _ in children]
        with self.engine.begin() as connection:
            child_states = []
            if child_ids:
                child_states = list(
                    connection.execute(
                        sa.select(source.c.status, source.c.docs_found).where(
                            source.c.workspace_id == workspace_id, source.c.id.in_(child_ids)
                        )
                    ).all()
                )
            statuses = [str(row.status) for row in child_states]
            docs_found = sum(int(row.docs_found) for row in child_states)
            parent_status = _aggregate_status(statuses)
            connection.execute(
                sa.update(source)
                .where(source.c.workspace_id == workspace_id, source.c.id == file_id)
                .values(
                    status=parent_status,
                    docs_found=docs_found,
                    parse_finished_at=sa.func.now() if parent_status != "queued" else None,
                    error_code=None if parent_status != "failed" else "ZIP_NO_SUCCESSFUL_ENTRIES",
                    error_message=None
                    if parent_status != "failed"
                    else "The archive did not contain any processable entries.",
                    version=source.c.version + 1,
                )
            )
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                stage="parsed",
                outcome="warn" if parent_status in {"partial", "failed"} else "ok",
                job_id=job_id,
                detail={
                    "child_count": len(child_ids),
                    "child_statuses": Counter(statuses),
                    "archive_scan_complete": True,
                },
            )

    @requires("files.upload")
    def parse_file(self, file_id: UUID, *, job_id: int | None = None) -> None:
        source = metadata.tables["source_file"]
        event_table = metadata.tables["processing_event"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(source).where(source.c.id == file_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["removed_at"] is not None:
                return
            resumed = False
            if row["status"] == "parsing" and job_id is not None:
                prior_starts = connection.execute(
                    sa.select(event_table.c.detail).where(
                        event_table.c.source_file_id == file_id,
                        event_table.c.job_id == job_id,
                        event_table.c.stage == "parsed",
                    )
                ).scalars()
                resumed = any(
                    isinstance(detail, Mapping) and detail.get("worker_started") is True
                    for detail in prior_starts
                )
                if not resumed:
                    return
            elif row["status"] != "queued":
                return
            connection.execute(
                sa.update(source)
                .where(source.c.id == file_id)
                .values(
                    status="parsing",
                    parse_started_at=sa.func.now(),
                    attempt_count=source.c.attempt_count + 1,
                    version=source.c.version + 1,
                )
            )
            _event(
                connection,
                workspace_id=cast(UUID, row["workspace_id"]),
                file_id=file_id,
                stage="parsed",
                outcome="warn",
                job_id=job_id,
                detail={"worker_started": True, "worker_resumed": resumed},
            )

        start = time.perf_counter()
        content = self._read_object(cast(str, row["object_key"]))
        if row["format"] == FileFormat.PDF.value:
            parser_hints = self._parser_hints(cast(UUID, row["workspace_id"]), row)
            result = parse_pdf_isolated(
                content,
                parser_hints,
                languages=self.ocr_languages,
                engine_name=self.extraction_engine,
                einvoice_keys=self.einvoice_keys,
            )
        elif row["zone"] == UploadZone.OPENING_BALANCE.value:
            result = ParseResult(
                success=False,
                file_format=FileFormat(row["format"]),
                layout_signature=hashlib.sha256(b"opening-balance:format-not-defined").hexdigest(),
                reason_code="OPENING_BALANCE_LAYOUT_REQUIRED",
                warnings=(
                    ParseWarning(
                        code="OPENING_BALANCE_LAYOUT_REQUIRED",
                        message="Opening-balance import needs an explicit column mapping.",
                    ),
                ),
            )
        else:
            parser_hints = self._parser_hints(cast(UUID, row["workspace_id"]), row)
            try:
                result = parse(content, parser_hints)
            except Exception:
                self._fail_file(
                    file_id,
                    "PARSER_INTERNAL_ERROR",
                    "The file could not be processed by the parser.",
                    job_id=job_id,
                )
                return
        duration_ms = max(0, int((time.perf_counter() - start) * 1000))

        try:
            self._persist_parse(file_row=row, result=result, duration_ms=duration_ms, job_id=job_id)
        except (BotoCoreError, OSError) as exc:
            raise ObjectStoreUnavailable from exc

    @requires("files.upload")
    def prepare_vision_pages(
        self, workspace_id: UUID, version_id: UUID, *, job_id: int | None = None
    ) -> str:
        """Render and mask a parsed version's pages, then queue the vision request.

        Runs in the general worker (the PDF sandbox has no network). Returns the
        outcome for logging: ``queued``, ``skipped`` (no longer ready, eligible or
        current) or ``failed`` (the pages could not be rendered; a skipped request
        records why).
        """

        del job_id
        hooks = self.ai_hooks
        if hooks is None:
            return "skipped"
        with self.engine.connect() as connection:
            subject = vision.load_subject(connection, workspace_id, version_id)
            targets = hooks.vision_targets(connection, workspace_id, [version_id])
        if subject is None or subject.object_key is None or version_id not in targets:
            return "skipped"
        content = self._read_object(subject.object_key)
        rendered = vision_pages_isolated(content, subject.pages, languages=self.ocr_languages)
        with self.engine.begin() as connection:
            current = vision.load_subject(connection, workspace_id, version_id, for_update=True)
            if current is None or not current.current:
                return "skipped"
            if rendered is None or [page.page for page in rendered] != list(subject.pages):
                hooks.vision_read(
                    connection,
                    workspace_id,
                    version_id,
                    subject_version=f"failed:{subject.content_hash}",
                    skip_code=vision.SKIP_PAGES_FAILED,
                )
                return "failed"
            vision.store_pages(connection, workspace_id, version_id, rendered)
            request_id = hooks.vision_read(
                connection,
                workspace_id,
                version_id,
                subject_version=vision.fingerprint(rendered),
            )
        # No new request (not ready any more, or the same one is already live): the AI
        # worker's sweep clears image bytes that no live request uses.
        return "queued" if request_id is not None else "skipped"

    def _parser_hints(self, workspace_id: UUID, file_row: Mapping[str, Any]) -> ParserHints:
        body = self._active_config_body(workspace_id)
        zone_type = {
            "po": DocType.PO,
            "grn": DocType.GRN,
            "invoice": DocType.INVOICE,
        }.get(str(file_row["zone"]))
        limits = self.limits(workspace_id)
        column_mapping: dict[str, str] = {}
        if file_row["format"] == FileFormat.CSV.value and file_row.get("layout_signature"):
            profile_table = metadata.tables["layout_profile"]
            with self.engine.connect() as connection:
                profile_body = connection.scalar(
                    sa.select(profile_table.c.body)
                    .where(
                        profile_table.c.workspace_id == workspace_id,
                        profile_table.c.signature == file_row["layout_signature"],
                        profile_table.c.kind == "csv_mapping",
                        profile_table.c.vendor_id.is_(None),
                    )
                    .limit(1)
                )
            if isinstance(profile_body, Mapping):
                saved_mapping = profile_body.get("column_mapping")
                if isinstance(saved_mapping, Mapping):
                    column_mapping = {
                        str(label): str(field)
                        for label, field in saved_mapping.items()
                        if isinstance(label, str) and isinstance(field, str)
                    }
        return parser_hints_from_config(
            body,
            zone_type=zone_type,
            file_format=FileFormat(file_row["format"]),
            limits=limits,
            page_split=_page_split_mode(file_row),
            column_mapping=column_mapping,
        )

    def _active_config_body(self, workspace_id: UUID) -> dict[str, Any]:
        active = metadata.tables["config_active"]
        version = metadata.tables["config_version"]
        with self.engine.connect() as connection:
            body = connection.scalar(
                sa.select(version.c.body)
                .select_from(
                    active.join(
                        version,
                        sa.and_(
                            active.c.workspace_id == version.c.workspace_id,
                            active.c.config_version_id == version.c.id,
                        ),
                    )
                )
                .where(active.c.workspace_id == workspace_id)
            )
        if not isinstance(body, dict):
            raise RuntimeError("active workspace configuration is unavailable")
        return body

    def _read_object(self, key: str) -> bytes:
        try:
            result = self.object_store.get(key)
            try:
                data = result.stream.read()
            finally:
                close = getattr(result.stream, "close", None)
                if callable(close):
                    close()
            return cast(bytes, data)
        except (BotoCoreError, OSError) as exc:
            raise ObjectStoreUnavailable from exc

    def _persist_parse(
        self,
        *,
        file_row: Mapping[str, Any],
        result: ParseResult,
        duration_ms: int,
        job_id: int | None,
    ) -> None:
        workspace_id = cast(UUID, file_row["workspace_id"])
        file_id = cast(UUID, file_row["id"])
        source = metadata.tables["source_file"]
        warning_codes = tuple(warning.code for warning in result.warnings)
        parse_failed = not result.success or not result.documents
        partial = bool(result.documents) and any(
            warning.level.value == "error"
            or warning.code in {"RECORD_PARSE_ERROR", "DOC_TYPE_REQUIRED", "NO_TEXT_LAYER"}
            for warning in result.warnings
        )
        status = "failed" if parse_failed else "partial" if partial else "parsed"
        error_code = result.reason_code if parse_failed else None
        with self.engine.begin() as connection:
            documents_new = documents_updated = documents_skipped = 0
            document_ids: list[UUID] = []
            pdf_version_ids: list[UUID] = []
            if not parse_failed:
                hints = self._parser_hints(workspace_id, file_row)
                for document in result.documents:
                    own_pages = split_pages(document)
                    document_page_hashes = tuple(
                        page_hash
                        for page_hash in result.page_image_hashes
                        if not own_pages or page_hash.page in own_pages
                    )
                    outcome, document_id, version_id = self._upsert_document(
                        connection,
                        workspace_id=workspace_id,
                        batch_id=cast(UUID | None, file_row["batch_id"]),
                        file_id=file_id,
                        actor_id=cast(UUID | None, file_row["uploaded_by"]),
                        document=document,
                        file_sha=cast(str, file_row["sha256"]),
                        page_hashes=document_page_hashes,
                        job_id=job_id,
                        parser_version=PARSER_VERSION,
                        layout_signature=result.layout_signature,
                        extraction_method=_extraction_method(
                            result.file_format,
                            is_ocr=any(page_text.is_ocr for page_text in result.page_texts),
                        ),
                        uom_aliases=hints.uom_aliases,
                        charge_keywords=hints.charge_keywords,
                    )
                    if outcome == "held":
                        # Not ingested: shown on the file as a (possible) duplicate (PD-0b-4).
                        documents_skipped += 1
                        continue
                    document_ids.append(document_id)
                    if outcome == "new":
                        documents_new += 1
                    elif outcome == "updated":
                        documents_updated += 1
                    else:
                        documents_skipped += 1
                    if version_id is not None and outcome != "skipped" and result.page_texts:
                        if result.file_format is FileFormat.PDF and outcome != "alternative":
                            pdf_version_ids.append(version_id)
                        text_table = metadata.tables["document_text"]
                        for page_text in result.page_texts:
                            if own_pages and page_text.page not in own_pages:
                                continue
                            connection.execute(
                                sa.insert(text_table).values(
                                    document_version_id=version_id,
                                    workspace_id=workspace_id,
                                    page=page_text.page,
                                    content=page_text.content,
                                    char_count=len(page_text.content),
                                    is_ocr=page_text.is_ocr,
                                )
                            )
                    matching_warnings = [
                        warning
                        for warning in result.warnings
                        if warning.locator == document.source_locator
                        or (
                            len(result.documents) == 1
                            and warning.locator == "pdf"
                            and document.source_locator is not None
                        )
                    ]
                    self._write_warnings(
                        connection,
                        workspace_id=workspace_id,
                        file_id=file_id,
                        document_id=document_id,
                        warning_rows=matching_warnings,
                        document=document,
                        document_version_id=version_id,
                    )
                self._record_page_split(
                    connection,
                    file_row=file_row,
                    result=result,
                    document_ids=document_ids,
                    job_id=job_id,
                )
                batch_id = cast(UUID | None, file_row["batch_id"])
                if batch_id is not None:
                    self._suggest_batch_label(connection, workspace_id, batch_id)
            self._write_warnings(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                document_id=None,
                warning_rows=list(result.warnings) if parse_failed else [],
                document=None,
            )
            connection.execute(
                sa.update(source)
                .where(source.c.workspace_id == workspace_id, source.c.id == file_id)
                .values(
                    status=status,
                    error_code=error_code,
                    error_message=(
                        next(
                            (warning.message for warning in result.warnings),
                            "The file could not be parsed.",
                        )
                        if parse_failed
                        # The open "split this file?" question, shown on the file row.
                        else next(
                            (
                                warning.message
                                for warning in result.warnings
                                if warning.code == "PAGE_SPLIT_SUGGESTED"
                            ),
                            None,
                        )
                    ),
                    layout_signature=result.layout_signature,
                    parser_version=PARSER_VERSION,
                    docs_found=len(result.documents),
                    docs_new=documents_new,
                    docs_updated=documents_updated,
                    docs_skipped=documents_skipped,
                    parse_finished_at=sa.func.now(),
                    version=source.c.version + 1,
                )
            )
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                stage="parsed",
                outcome="error" if parse_failed else ("warn" if warning_codes else "ok"),
                duration_ms=duration_ms,
                parser_version=PARSER_VERSION,
                warning_codes=warning_codes,
                error_code=error_code,
                job_id=job_id,
                detail={"documents": len(result.documents), "status": status},
            )
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                stage="qa",
                outcome="warn" if warning_codes else "ok",
                duration_ms=duration_ms,
                parser_version=PARSER_VERSION,
                warning_codes=warning_codes,
                job_id=job_id,
                detail={"warning_count": len(warning_codes)},
            )
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                stage="gated",
                outcome="error" if parse_failed else "ok",
                parser_version=PARSER_VERSION,
                warning_codes=warning_codes,
                error_code=error_code,
                job_id=job_id,
                detail={"status": status, "document_ids": [str(item) for item in document_ids]},
            )
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                stage="upserted",
                outcome="error" if parse_failed else "ok",
                parser_version=PARSER_VERSION,
                warning_codes=warning_codes,
                error_code=error_code,
                job_id=job_id,
                detail={
                    "new": documents_new,
                    "updated": documents_updated,
                    "skipped": documents_skipped,
                },
            )
            if documents_new or documents_updated:
                self.queue.enqueue("resolve_vendors", file_id=file_id, connection=connection)
            self._queue_extraction_suggestions(connection, workspace_id, pdf_version_ids)
        if file_row["parent_file_id"] is not None:
            self._refresh_parent(cast(UUID, file_row["parent_file_id"]))

    def _record_page_split(
        self,
        connection: Connection,
        *,
        file_row: Mapping[str, Any],
        result: ParseResult,
        document_ids: Sequence[UUID],
        job_id: int | None,
    ) -> None:
        """Open the "split this file?" question, or settle an answered one (AC-3, D-9)."""

        workspace_id = cast(UUID, file_row["workspace_id"])
        file_id = cast(UUID, file_row["id"])
        if file_row["format"] != FileFormat.PDF.value:
            return
        source = metadata.tables["source_file"]
        warning_table = metadata.tables["document_warning"]
        document = metadata.tables["document"]
        document_version = metadata.tables["document_version"]
        suggested = next(
            (warning for warning in result.warnings if warning.code == "PAGE_SPLIT_SUGGESTED"),
            None,
        )
        connection.execute(
            sa.update(warning_table)
            .where(
                warning_table.c.workspace_id == workspace_id,
                warning_table.c.source_file_id == file_id,
                warning_table.c.code == "PAGE_SPLIT_SUGGESTED",
                warning_table.c.resolved_at.is_(None),
            )
            .values(resolved_at=sa.func.now())
        )
        if suggested is not None:
            groups = [
                {
                    "document_id": str(document_id),
                    "doc_type": parsed.header.doc_type.value,
                    "pages": list(split_pages(parsed)),
                    "doc_no": str(parsed.header.doc_no.value) if parsed.header.doc_no else None,
                }
                for parsed, document_id in zip(result.documents, document_ids, strict=True)
            ]
            connection.execute(
                sa.insert(warning_table).values(
                    id=new_id(),
                    workspace_id=workspace_id,
                    source_file_id=file_id,
                    code="PAGE_SPLIT_SUGGESTED",
                    level="warning",
                    message=suggested.message,
                    detail={"zone_uploaded": str(file_row["zone"]), "groups": groups},
                )
            )
            connection.execute(
                sa.update(source)
                .where(source.c.workspace_id == workspace_id, source.c.id == file_id)
                .values(mismatch_state="suggested")
            )
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                stage="gated",
                outcome="warn",
                warning_codes=("PAGE_SPLIT_SUGGESTED",),
                job_id=job_id,
                detail={"page_split_suggested": True, "documents": len(groups)},
            )
            return
        if str(file_row["mismatch_state"]) not in {"accepted", "kept"}:
            return
        # After an answer the file is read again; documents only the earlier
        # reading produced fall back to their other sources or are withdrawn.
        conditions = [
            document.c.workspace_id == workspace_id,
            document.c.id.in_(
                sa.select(document_version.c.document_id).where(
                    document_version.c.workspace_id == workspace_id,
                    document_version.c.source_file_id == file_id,
                )
            ),
            document.c.withdrawn_at.is_(None),
        ]
        if document_ids:
            conditions.append(document.c.id.not_in(list(document_ids)))
        stale = (
            connection.execute(sa.select(document).where(*conditions).with_for_update())
            .mappings()
            .all()
        )
        if stale:
            self._settle_documents(
                connection,
                workspace_id=workspace_id,
                actor_id=None,
                file_id=file_id,
                rows=stale,
                exclude_file=True,
                reason="page split answered",
            )

    @requires("files.upload")
    def resolve_page_split(
        self,
        actor: ActorContext,
        file_id: UUID,
        *,
        accept: bool,
        expected_version: int,
    ) -> dict[str, Any]:
        """Accept the proposed documents, or keep the file as one document of its zone.

        Either answer reads the file again, so the stored versions carry it.
        """

        table = metadata.tables["source_file"]
        warning = metadata.tables["document_warning"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(table)
                    .where(
                        table.c.workspace_id == actor.workspace_id,
                        table.c.id == file_id,
                        table.c.removed_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise IngestionFailure("FILE_NOT_FOUND", "The file was not found.")
            if row["version"] != expected_version:
                raise BatchVersionConflict
            pending = connection.scalar(
                sa.select(warning.c.id)
                .where(
                    warning.c.workspace_id == actor.workspace_id,
                    warning.c.source_file_id == file_id,
                    warning.c.code == "PAGE_SPLIT_SUGGESTED",
                    warning.c.resolved_at.is_(None),
                )
                .limit(1)
            )
            if row["mismatch_state"] != "suggested" or pending is None:
                raise IngestionFailure("PAGE_SPLIT_NOT_PENDING", "No page split is pending.")
            state = "accepted" if accept else "kept"
            updated = (
                connection.execute(
                    sa.update(table)
                    .where(table.c.id == file_id)
                    .values(
                        mismatch_state=state,
                        status="queued",
                        error_code=None,
                        error_message=None,
                        version=table.c.version + 1,
                    )
                    .returning(*table.c)
                )
                .mappings()
                .one()
            )
            connection.execute(
                sa.update(warning)
                .where(
                    warning.c.workspace_id == actor.workspace_id,
                    warning.c.source_file_id == file_id,
                    warning.c.code == "PAGE_SPLIT_SUGGESTED",
                    warning.c.resolved_at.is_(None),
                )
                .values(resolved_at=sa.func.now(), resolved_by=actor.actor_id)
            )
            job_id = self.queue.enqueue("parse_file", file_id=file_id, connection=connection)
            write_audit_event(
                connection,
                actor,
                action="file.page_split.resolve",
                target_type="file",
                target_id=file_id,
                before_state={"mismatch_state": "suggested"},
                after_state={"mismatch_state": state},
            )
            _event(
                connection,
                workspace_id=actor.workspace_id,
                file_id=file_id,
                stage="sniffed",
                outcome="ok",
                job_id=job_id,
                detail={"page_split_resolution": state},
            )
        return dict(updated)

    def _queue_extraction_suggestions(
        self, connection: Connection, workspace_id: UUID, version_ids: Sequence[UUID]
    ) -> None:
        """Queue advisory H1 suggestions or vision page preparation; never blocks parsing.

        With page images on for the workspace the vision reader replaces H1 (the
        consented hook list then has no H1), so at most one of them is queued.
        """

        if self.ai_hooks is None or not version_ids:
            return
        enqueue_vision = getattr(self.queue, "enqueue_vision_pages", None)
        if enqueue_vision is not None:
            try:
                with connection.begin_nested():
                    for version_id in self.ai_hooks.vision_targets(
                        connection, workspace_id, version_ids
                    ):
                        enqueue_vision(
                            workspace_id=workspace_id,
                            document_version_id=version_id,
                            connection=connection,
                        )
            except Exception as exc:  # noqa: BLE001 - AI is optional; rules continue.
                _LOGGER.warning(
                    "ai_enqueue_failed hook=h6_vision error_type=%s", type(exc).__name__
                )
        try:
            with connection.begin_nested():
                self.ai_hooks.extraction_suggestions(connection, workspace_id, version_ids)
        except Exception as exc:  # noqa: BLE001 - AI is optional; rules continue.
            _LOGGER.warning("ai_enqueue_failed hook=h1_extract error_type=%s", type(exc).__name__)

    @staticmethod
    def _suggest_batch_label(connection: Connection, workspace_id: UUID, batch_id: UUID) -> None:
        """Suggest the dominant invoice month while the user has not named the batch."""

        batch = metadata.tables["batch"]
        document = metadata.tables["document"]
        version = metadata.tables["document_version"]
        batch_is_suggested = connection.scalar(
            sa.select(batch.c.label_is_suggested).where(
                batch.c.workspace_id == workspace_id,
                batch.c.id == batch_id,
            )
        )
        if batch_is_suggested is not True:
            return
        invoice_month = sa.func.date_trunc("month", version.c.doc_date)
        most_common = connection.execute(
            sa.select(invoice_month.label("month"))
            .select_from(
                document.join(
                    version,
                    sa.and_(
                        document.c.workspace_id == version.c.workspace_id,
                        document.c.current_version_id == version.c.id,
                    ),
                )
            )
            .where(
                document.c.workspace_id == workspace_id,
                document.c.batch_id == batch_id,
                document.c.doc_type == DocType.INVOICE.value,
                document.c.withdrawn_at.is_(None),
                version.c.doc_date.is_not(None),
            )
            .group_by(invoice_month)
            .order_by(sa.func.count().desc(), invoice_month.asc())
            .limit(1)
        ).scalar_one_or_none()
        if most_common is None:
            return
        month_names = (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        )
        label = f"{month_names[most_common.month - 1]} {most_common.year}"
        connection.execute(
            sa.update(batch)
            .where(
                batch.c.workspace_id == workspace_id,
                batch.c.id == batch_id,
                batch.c.label_is_suggested.is_(True),
            )
            .values(label=label, updated_at=sa.func.now(), version=batch.c.version + 1)
        )

    def _invoice_duplicate_gate(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        file_id: UUID,
        actor_id: UUID | None,
        document: Document,
        existing: Mapping[str, Any] | None,
        job_id: int | None,
        structured: bool,
    ) -> tuple[UUID | None, Mapping[str, Any] | None]:
        """Hold an invoice that matches an active one; apply a person's decision (PD-0b-4).

        Returns the original's id when the document is not ingested (an open or discarded
        hold, or a new hold), else the document to update (``None`` for a new one).
        """

        document_table = metadata.tables["document"]
        version_table = metadata.tables["document_version"]
        locator = document.source_locator or ""
        hold = hold_for(connection, workspace_id=workspace_id, file_id=file_id, locator=locator)
        active = (
            existing
            if existing is not None
            and existing["withdrawn_at"] is None
            and existing["current_version_id"] is not None
            else None
        )
        if active is not None:
            source_of_current = connection.scalar(
                sa.select(version_table.c.source_file_id).where(
                    version_table.c.id == active["current_version_id"]
                )
            )
            if source_of_current == file_id:
                return None, existing  # the same file read again (parser refresh, decision)
        if hold is not None:
            original_id = cast(UUID, hold["duplicate_of_document_id"])
            if hold["state"] == "replaced":
                replaced = (
                    connection.execute(
                        sa.select(document_table)
                        .where(
                            document_table.c.workspace_id == workspace_id,
                            document_table.c.id == original_id,
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .one()
                )
                return None, replaced
            if hold["state"] == "kept":
                return None, (None if active is not None else existing)
            return original_id, existing
        match = find_invoice_duplicate(
            connection,
            workspace_id=workspace_id,
            document=document,
            rescan_match=active,
            structured=structured,
        )
        if match is None:
            return None, existing
        hold_table = metadata.tables["duplicate_hold"]
        hold_id = new_id()
        connection.execute(
            pg_insert(hold_table)
            .values(
                id=hold_id,
                workspace_id=workspace_id,
                source_file_id=file_id,
                source_locator=locator,
                kind=match.kind,
                reason=match.reason,
                duplicate_of_document_id=match.original_id,
                doc_no=doc_no_text(document),
            )
            .on_conflict_do_nothing(constraint="duplicate_hold_document_key")
        )
        _event(
            connection,
            workspace_id=workspace_id,
            file_id=file_id,
            stage="upserted",
            outcome="warn",
            job_id=job_id,
            detail={
                "outcome": "held",
                "kind": match.kind,
                "reason": match.reason,
                "duplicate_of_document_id": str(match.original_id),
            },
        )
        if actor_id is not None:
            user = metadata.tables["app_user"]
            role = connection.scalar(sa.select(user.c.role).where(user.c.id == actor_id))
            if role is not None:
                write_audit_event(
                    connection,
                    ActorContext(workspace_id, actor_id, cast(Any, role)),
                    action="document.duplicate_held",
                    target_type="file",
                    target_id=file_id,
                    after_state={
                        "kind": match.kind,
                        "reason": match.reason,
                        "duplicate_of_document_id": str(match.original_id),
                        "hold_id": str(hold_id),
                    },
                )
        return match.original_id, existing

    def _upsert_document(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        batch_id: UUID | None,
        file_id: UUID,
        actor_id: UUID | None,
        document: Document,
        file_sha: str,
        page_hashes: tuple[PageImageHash, ...] = (),
        job_id: int | None,
        parser_version: str,
        layout_signature: str,
        extraction_method: ExtractionMethod,
        uom_aliases: Mapping[str, str],
        charge_keywords: tuple[str, ...],
    ) -> tuple[str, UUID, UUID | None]:
        document_table = metadata.tables["document"]
        version_table = metadata.tables["document_version"]
        header = document.header
        header_values = _header_values(header)
        doc_no = _plain(header_values.get("doc_no"))
        doc_date = _plain(header_values.get("doc_date"))
        canonical_doc_no = canonicalize_document_number(doc_no) if doc_no else None
        gstin = _plain(header_values.get("vendor_gstin"))
        vendor_name = _plain(header_values.get("vendor_name"))
        vendor_identity = _vendor_identity(gstin, vendor_name)
        content_value = document.model_dump(mode="json")
        content_hash = _canonical_hash(content_value)
        structured = extraction_method in {
            ExtractionMethod.XML_PROFILE,
            ExtractionMethod.CSV_MAPPING,
            ExtractionMethod.MANUAL,
        }
        rescan_identity = rescan_identity_payload(
            document, file_sha, page_hashes=page_hashes, structured=structured
        )
        natural_key = document_natural_key(
            document, file_sha, page_hashes=page_hashes, structured=structured
        )
        natural_identity = identity_payload(
            document, file_sha, page_hashes=page_hashes, structured=structured
        )
        content_identity = content_fingerprint(document, structured=structured)
        incoming_verified_confidence = verified_field_confidence(document, structured=structured)
        identity_table = metadata.tables["document_identity"]
        identity_query = (
            sa.select(version_table.c.document_id)
            .join(
                identity_table,
                identity_table.c.document_version_id == version_table.c.id,
            )
            .join(document_table, document_table.c.id == version_table.c.document_id)
            .where(
                identity_table.c.workspace_id == workspace_id,
                version_table.c.workspace_id == workspace_id,
                document_table.c.workspace_id == workspace_id,
                document_table.c.doc_type == header.doc_type.value,
            )
        )
        if rescan_identity["kind"] == "fingerprint":
            exact_query = identity_query.where(
                identity_table.c.content_fingerprint == content_identity,
                identity_table.c.page_hashes
                == [item.value for item in sorted(page_hashes, key=lambda item: item.page)],
            )
            candidate_ids = set(connection.scalars(exact_query.distinct()).all())
            if not candidate_ids:
                # Exact SHA + locator is stronger than a bounded perceptual
                # match when earlier versions lack captured page hashes.
                legacy_query = identity_query.where(
                    sa.or_(
                        identity_table.c.content_fingerprint.is_(None),
                        identity_table.c.page_hashes == [],
                    ),
                    identity_table.c.source_sha256 == file_sha,
                    identity_table.c.source_locator == document.source_locator,
                )
                candidate_ids = set(connection.scalars(legacy_query.distinct()).all())
            if not candidate_ids:
                fingerprint_rows = connection.execute(
                    identity_query.with_only_columns(
                        version_table.c.document_id,
                        identity_table.c.page_hashes,
                    ).where(identity_table.c.content_fingerprint == content_identity)
                ).all()
                incoming_hashes = [
                    item.value for item in sorted(page_hashes, key=lambda item: item.page)
                ]
                candidate_ids = {
                    cast(UUID, row.document_id)
                    for row in fingerprint_rows
                    if page_hashes_within_distance(tuple(row.page_hashes or ()), incoming_hashes)
                }
        else:
            source_query = identity_query.where(
                identity_table.c.source_sha256 == file_sha,
                identity_table.c.source_locator == document.source_locator,
            )
            candidate_ids = set(connection.scalars(source_query.distinct()).all())
        if not candidate_ids and natural_identity["kind"] == "verified":
            verified_identity_query = sa.select(document_table.c.id).where(
                document_table.c.workspace_id == workspace_id,
                document_table.c.doc_type == header.doc_type.value,
                document_table.c.natural_key == natural_key,
            )
            candidate_ids = set(connection.scalars(verified_identity_query.limit(2)).all())
        existing = None
        if len(candidate_ids) == 1:
            existing = (
                connection.execute(
                    sa.select(document_table)
                    .where(
                        document_table.c.workspace_id == workspace_id,
                        document_table.c.id == next(iter(candidate_ids)),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
        if (
            existing is None
            and structured
            and canonical_doc_no
            and header.doc_type in {DocType.PO, DocType.GRN}
        ):
            # PO/GRN identity is the type and canonical number (PD-0b-4): a changed
            # re-export is an amendment of the active document, never a second one.
            existing = active_structured_by_number(
                connection,
                workspace_id=workspace_id,
                doc_type=header.doc_type,
                canonical_doc_no=canonical_doc_no,
                vendor_identity=vendor_identity,
            )
        if header.doc_type is DocType.INVOICE:
            held_as, existing = self._invoice_duplicate_gate(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                actor_id=actor_id,
                document=document,
                existing=existing,
                job_id=job_id,
                structured=structured,
            )
            if held_as is not None:
                return "held", held_as, None
        if existing is None:
            natural_key_owner = connection.scalar(
                sa.select(document_table.c.id)
                .where(
                    document_table.c.workspace_id == workspace_id,
                    document_table.c.natural_key == natural_key,
                )
                .limit(1)
            )
            if natural_key_owner is not None:
                natural_key = document_identity_collision_key(
                    document,
                    file_sha,
                    page_hashes=page_hashes,
                    structured=structured,
                )
        created = existing is None
        document_id = cast(UUID, existing["id"]) if existing is not None else new_id()
        current_version_id = cast(UUID | None, existing["current_version_id"]) if existing else None
        current_hash = None
        current_source_file_id: UUID | None = None
        alternative_version = False
        current_verified_confidence: Decimal | None = None
        if current_version_id is not None:
            current_snapshot = (
                connection.execute(
                    sa.select(
                        version_table.c.content_hash,
                        version_table.c.extraction_method,
                        version_table.c.source_file_id,
                        version_table.c.parser_version,
                        version_table.c.needs_verification,
                    ).where(
                        version_table.c.workspace_id == workspace_id,
                        version_table.c.id == current_version_id,
                    )
                )
                .mappings()
                .one()
            )
            document, verified_reference_conflict = _preserve_user_verified_references(
                connection,
                workspace_id=workspace_id,
                version_id=current_version_id,
                document=document,
            )
            if not verified_reference_conflict:
                header = document.header
                header_values = _header_values(header)
                content_value = document.model_dump(mode="json")
                content_hash = _canonical_hash(content_value)
                incoming_verified_confidence = verified_field_confidence(
                    document, structured=structured
                )
            current_hash = cast(str, current_snapshot["content_hash"])
            current_source_file_id = cast(UUID, current_snapshot["source_file_id"])
            current_verified_confidence = _persisted_verified_field_confidence(
                connection,
                workspace_id=workspace_id,
                version_id=current_version_id,
                extraction_method=str(current_snapshot["extraction_method"]),
            )
            if (
                current_snapshot["extraction_method"] == ExtractionMethod.MANUAL.value
                and current_snapshot["source_file_id"] == file_id
                and current_snapshot["parser_version"] == parser_version
            ):
                _event(
                    connection,
                    workspace_id=workspace_id,
                    file_id=file_id,
                    document_id=document_id,
                    document_version_id=current_version_id,
                    stage="upserted",
                    outcome="skipped",
                    parser_version=parser_version,
                    job_id=job_id,
                    detail={"outcome": "skipped", "reason": "manual_review_version_preserved"},
                )
                return "skipped", document_id, current_version_id
            alternative_version = verified_reference_conflict or (
                current_verified_confidence is not None
                and (
                    incoming_verified_confidence is None
                    or incoming_verified_confidence < current_verified_confidence
                )
            )
            if split_pages(document) and current_snapshot["extraction_method"] in {
                ExtractionMethod.XML_PROFILE.value,
                ExtractionMethod.CSV_MAPPING.value,
                ExtractionMethod.MANUAL.value,
            }:
                # A bundle page printed from a structured record or a person's entry
                # never replaces it (accuracy plan AC-3); AC-8 generalises this guard.
                _event(
                    connection,
                    workspace_id=workspace_id,
                    file_id=file_id,
                    document_id=document_id,
                    document_version_id=current_version_id,
                    stage="upserted",
                    outcome="skipped",
                    parser_version=parser_version,
                    job_id=job_id,
                    detail={"outcome": "skipped", "reason": "structured_version_preserved"},
                )
                return "skipped", document_id, current_version_id
        if (
            structured
            and existing is not None
            and existing["withdrawn_at"] is None
            and current_hash == content_hash
            and current_source_file_id != file_id
        ):
            # The same PO/GRN, unchanged, in another export: no new version and no ledger
            # work (PD-0b-4). The event keeps it alive if its first file is removed later.
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                document_id=document_id,
                document_version_id=current_version_id,
                stage="upserted",
                outcome="skipped",
                parser_version=parser_version,
                job_id=job_id,
                detail={
                    "outcome": "skipped",
                    "reason": "unchanged_in_other_file",
                    "current_source_file_id": str(current_source_file_id),
                },
            )
            return "skipped", document_id, current_version_id
        if current_hash == content_hash and current_source_file_id == file_id:
            if existing is not None and existing["withdrawn_at"] is not None:
                connection.execute(
                    sa.update(document_table)
                    .where(
                        document_table.c.workspace_id == workspace_id,
                        document_table.c.id == document_id,
                    )
                    .values(
                        withdrawn_at=None, withdrawn_by=None, version=document_table.c.version + 1
                    )
                )
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                document_id=document_id,
                document_version_id=current_version_id,
                stage="upserted",
                outcome="skipped",
                parser_version=parser_version,
                job_id=job_id,
                detail={"outcome": "skipped", "content_hash": content_hash},
            )
            return "skipped", document_id, current_version_id

        if existing is None:
            connection.execute(
                sa.insert(document_table).values(
                    id=document_id,
                    workspace_id=workspace_id,
                    doc_type=header.doc_type.value,
                    natural_key=natural_key,
                    batch_id=batch_id,
                    vendor_identity=vendor_identity,
                    doc_no=doc_no,
                    canonical_doc_no=canonical_doc_no,
                    doc_date=doc_date,
                    grand_total=_plain(header_values.get("grand_total")),
                    needs_verification=document.needs_verification,
                )
            )

        latest_seq = connection.scalar(
            sa.select(sa.func.max(version_table.c.seq)).where(
                version_table.c.workspace_id == workspace_id,
                version_table.c.document_id == document_id,
            )
        )
        seq = int(latest_seq or 0) + 1
        version_id = new_id()
        revision = _plain(header_values.get("revision"))
        po_refs = _reference_values(header_values.get("po_refs"))
        grn_refs = _reference_values(header_values.get("grn_refs"))
        payment_terms = _plain(header_values.get("payment_terms"))
        version_values = {
            "id": version_id,
            "workspace_id": workspace_id,
            "document_id": document_id,
            "seq": seq,
            "revision": revision,
            "revision_rank": _revision_rank(revision),
            "content_hash": content_hash,
            "doc_no": doc_no,
            "canonical_doc_no": canonical_doc_no,
            "doc_date": doc_date,
            "po_status": _enum_value(_plain(header_values.get("status"))),
            "vendor_name": vendor_name,
            "vendor_name_norm": normalize_vendor_name(vendor_name) if vendor_name else None,
            "vendor_gstin": gstin,
            "vendor_pan": gstin[2:12] if gstin and len(gstin) == 15 else None,
            "vendor_state_code": gstin[:2] if gstin and len(gstin) == 15 else None,
            "buyer_gstin": _plain(header_values.get("buyer_gstin")),
            "ship_to": _plain(header_values.get("ship_to")),
            "place_of_supply": _plain(header_values.get("place_of_supply")),
            "payment_terms_text": payment_terms,
            "payment_terms_days": _payment_days(payment_terms),
            "delivery_date": _plain(header_values.get("delivery_date")),
            "currency": _plain(header_values.get("currency")) or "INR",
            "taxable_total": _plain(header_values.get("taxable_total")),
            "cgst_total": _plain(header_values.get("cgst")),
            "sgst_total": _plain(header_values.get("sgst")),
            "igst_total": _plain(header_values.get("igst")),
            "round_off": _plain(header_values.get("round_off")),
            "grand_total": _plain(header_values.get("grand_total")),
            "po_refs": po_refs,
            "canonical_po_refs": [canonicalize_document_number(ref) for ref in po_refs],
            "grn_refs": grn_refs,
            "canonical_grn_refs": [canonicalize_document_number(ref) for ref in grn_refs],
            "challan_no": _plain(header_values.get("challan_no")),
            "canonical_challan_no": _canonical_optional(header_values.get("challan_no")),
            "e_way_bill_no": _plain(header_values.get("e_way_bill_no")),
            "irn": _plain(header_values.get("irn")),
            "supplier_invoice_no": _plain(header_values.get("supplier_invoice_no")),
            "canonical_supplier_invoice_no": _canonical_optional(
                header_values.get("supplier_invoice_no")
            ),
            "extraction_method": extraction_method.value,
            "extraction_conf": document.confidence,
            "needs_verification": document.needs_verification,
            "verification_reasons": [reason.value for reason in document.reason_codes],
            "extras": _jsonable(
                {
                    "header": header.extras,
                    "document": document.model_dump(mode="json").get("extras", {}),
                }
            ),
            "source_file_id": file_id,
            "locator": _jsonable({"source": document.source_locator})
            if document.source_locator
            else None,
            "parser_version": parser_version,
            "layout_signature": layout_signature,
            "created_by": actor_id,
        }
        connection.execute(sa.insert(version_table).values(**version_values))
        identity_table = metadata.tables["document_identity"]
        connection.execute(
            sa.insert(identity_table).values(
                workspace_id=workspace_id,
                document_version_id=version_id,
                source_sha256=file_sha,
                source_locator=document.source_locator,
                page_hashes=[
                    item.value for item in sorted(page_hashes, key=lambda item: item.page)
                ],
                content_fingerprint=content_identity,
            )
        )
        self._insert_lines_and_evidence(
            connection,
            workspace_id=workspace_id,
            version_id=version_id,
            document=document,
            extraction_method=extraction_method,
            uom_aliases=uom_aliases,
            charge_keywords=charge_keywords,
        )
        if not alternative_version:
            identity_key_owner = None
            if existing is not None and existing["natural_key"] != natural_key:
                identity_key_owner = connection.scalar(
                    sa.select(document_table.c.id)
                    .where(
                        document_table.c.workspace_id == workspace_id,
                        document_table.c.natural_key == natural_key,
                        document_table.c.id != document_id,
                    )
                    .limit(1)
                )
            connection.execute(
                sa.update(document_table)
                .where(
                    document_table.c.workspace_id == workspace_id,
                    document_table.c.id == document_id,
                )
                .values(
                    current_version_id=version_id,
                    natural_key=(
                        natural_key if identity_key_owner is None else document_table.c.natural_key
                    ),
                    batch_id=sa.func.coalesce(document_table.c.batch_id, batch_id),
                    vendor_identity=vendor_identity,
                    doc_no=doc_no,
                    canonical_doc_no=canonical_doc_no,
                    doc_date=doc_date,
                    grand_total=_plain(header_values.get("grand_total")),
                    needs_verification=document.needs_verification,
                    withdrawn_at=None,
                    withdrawn_by=None,
                    updated_at=sa.func.now(),
                    version=document_table.c.version + 1,
                )
            )
        if alternative_version:
            _event(
                connection,
                workspace_id=workspace_id,
                file_id=file_id,
                document_id=document_id,
                document_version_id=version_id,
                stage="upserted",
                outcome="warn",
                parser_version=parser_version,
                job_id=job_id,
                detail={
                    "outcome": "alternative_reading",
                    "reason": "verified_field_confidence_lower",
                    "current_version_id": str(current_version_id),
                },
            )
            return "alternative", document_id, version_id
        if header.doc_type is DocType.PO:
            connection.execute(
                sa.update(document_table)
                .where(
                    document_table.c.workspace_id == workspace_id,
                    document_table.c.id == document_id,
                )
                .values(input_seq=document_table.c.input_seq + 1)
            )
            self._mark_dirty_scope(
                connection,
                workspace_id=workspace_id,
                scope_kind="po",
                scope_id=document_id,
                reason="purchase order parsed or updated",
            )
        outcome = "new" if created else "updated"
        _event(
            connection,
            workspace_id=workspace_id,
            file_id=file_id,
            document_id=document_id,
            document_version_id=version_id,
            stage="upserted",
            outcome="ok",
            parser_version=parser_version,
            job_id=job_id,
            detail={"outcome": outcome, "content_hash": content_hash},
        )
        return outcome, document_id, version_id

    @staticmethod
    def _insert_lines_and_evidence(
        connection: Connection,
        *,
        workspace_id: UUID,
        version_id: UUID,
        document: Document,
        extraction_method: ExtractionMethod,
        uom_aliases: Mapping[str, str],
        charge_keywords: tuple[str, ...],
    ) -> None:
        line_table = metadata.tables["doc_line"]
        evidence_table = metadata.tables["field_evidence"]

        def evidence_provenance(value: FieldValue[Any]) -> dict[str, Any]:
            origin = _field_origin(value, extraction_method)
            return {
                "confidence": value.confidence if origin is not FieldOrigin.MANUAL else None,
                "origin": origin.value if origin is not None else None,
                "verification": value.verification.value,
                "verified_by": value.verified_by,
                "verified_at": value.verified_at,
            }

        for field_name in type(document.header).model_fields:
            value = getattr(document.header, field_name)
            if isinstance(value, FieldValue):
                connection.execute(
                    sa.insert(evidence_table).values(
                        id=new_id(),
                        workspace_id=workspace_id,
                        document_version_id=version_id,
                        field_name=field_name,
                        value_text=_field_text(value.value),
                        **evidence_provenance(value),
                        source=value.source.value,
                        page=value.evidence.page,
                        bbox=_jsonable(value.evidence.bbox) if value.evidence.bbox else None,
                        snippet=value.evidence.snippet,
                    )
                )
        for line in document.lines:
            line_id = new_id()
            values = {name: getattr(line, name) for name in type(line).model_fields}
            description = _plain(values.get("description"))
            normalized = normalize_item_text(description) if description else None
            original_uom = _plain(values.get("uom"))
            normalized_uom = normalize_uom(original_uom, uom_aliases) if original_uom else None
            line_type = _enum_value(_plain(values.get("line_type")))
            if line_type is None:
                line_type = (
                    "charge"
                    if description
                    and any(term.casefold() in description.casefold() for term in charge_keywords)
                    else "goods"
                )
            connection.execute(
                sa.insert(line_table).values(
                    id=line_id,
                    workspace_id=workspace_id,
                    document_version_id=version_id,
                    line_no=line.line_no,
                    line_type=line_type,
                    description=description,
                    description_norm=normalized.description if normalized else None,
                    tokens=list(normalized.tokens) if normalized else [],
                    spec_tokens=list(normalized.spec_tokens) if normalized else [],
                    item_code=_plain(values.get("item_code")),
                    hsn_sac=_plain(values.get("hsn_sac")),
                    uom=normalized_uom.canonical if normalized_uom else None,
                    uom_as_read=original_uom,
                    quantity=_plain(values.get("quantity")),
                    rate=_plain(values.get("rate")),
                    discount=_plain(values.get("discount")),
                    amount=_plain(values.get("amount")),
                    tax_rate=_plain(values.get("tax_rate")),
                    cgst_amount=_plain(values.get("cgst_amount")),
                    sgst_amount=_plain(values.get("sgst_amount")),
                    igst_amount=_plain(values.get("igst_amount")),
                    po_ref=_plain(values.get("po_ref")),
                    canonical_po_ref=_canonical_optional(values.get("po_ref")),
                    po_line_ref=_plain(values.get("po_line_ref")),
                    received_qty=_plain(values.get("received_qty")),
                    rejected_qty=_plain(values.get("rejected_qty")),
                    accepted_qty=_plain(values.get("accepted_qty")),
                    extras=_jsonable(values.get("extras") or {}),
                )
            )
            for field_name in type(line).model_fields:
                value = getattr(line, field_name)
                if isinstance(value, FieldValue):
                    connection.execute(
                        sa.insert(evidence_table).values(
                            id=new_id(),
                            workspace_id=workspace_id,
                            doc_line_id=line_id,
                            field_name=field_name,
                            value_text=_field_text(value.value),
                            **evidence_provenance(value),
                            source=value.source.value,
                            page=value.evidence.page,
                            bbox=_jsonable(value.evidence.bbox) if value.evidence.bbox else None,
                            snippet=value.evidence.snippet,
                        )
                    )

    @staticmethod
    def _write_warnings(
        connection: Connection,
        *,
        workspace_id: UUID,
        file_id: UUID,
        document_id: UUID | None,
        warning_rows: list[ParseWarning],
        document: Document | None,
        document_version_id: UUID | None = None,
    ) -> None:
        warnings: list[tuple[str, str, str, dict[str, Any] | None]] = [
            (
                warning.code,
                warning.level.value,
                warning.message,
                {"locator": warning.locator} if warning.locator else None,
            )
            for warning in warning_rows
        ]
        if document is not None:
            header_values = _header_values(document.header)
            if document.header.doc_type is DocType.INVOICE and not _reference_values(
                header_values.get("po_refs")
            ):
                warnings.append(
                    ("NO_PO_REF", "warning", "No purchase-order reference was extracted.", None)
                )
            if not _plain(header_values.get("vendor_gstin")):
                warnings.append(("NO_GSTIN", "warning", "No vendor GSTIN was extracted.", None))
            if document.needs_verification:
                warnings.append(
                    ("LOW_CONFIDENCE", "warning", "The document needs human verification.", None)
                )
        if document is None and warning_rows:
            warnings.append(
                (
                    "UNPARSEABLE",
                    "error",
                    "The source file could not be parsed into a document.",
                    None,
                )
            )

        table = metadata.tables["document_warning"]
        for code, level, message, detail in warnings:
            duplicate = connection.scalar(
                sa.select(table.c.id)
                .where(
                    table.c.workspace_id == workspace_id,
                    table.c.source_file_id == file_id,
                    table.c.document_id.is_(document_id)
                    if document_id is None
                    else table.c.document_id == document_id,
                    table.c.code == code,
                    table.c.resolved_at.is_(None),
                )
                .limit(1)
            )
            if duplicate is not None:
                continue
            connection.execute(
                sa.insert(table).values(
                    id=new_id(),
                    workspace_id=workspace_id,
                    document_id=document_id,
                    document_version_id=document_version_id,
                    source_file_id=file_id,
                    code=code,
                    level=level,
                    message=message,
                    detail=detail,
                )
            )

    def _mark_dirty_scope(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        scope_kind: str,
        scope_id: UUID,
        reason: str,
    ) -> None:
        table = metadata.tables["dirty_scope"]
        statement = pg_insert(table).values(
            id=new_id(),
            workspace_id=workspace_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            reason=reason,
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.workspace_id, table.c.scope_kind, table.c.scope_id],
                index_where=table.c.processed_at.is_(None),
                set_={
                    "mark_count": table.c.mark_count + 1,
                    "last_marked_at": sa.func.now(),
                    "reason": statement.excluded.reason,
                },
            )
        )
        enqueue_pending = getattr(self.queue, "enqueue_pending_recomputes", None)
        if callable(enqueue_pending):
            enqueue_pending(workspace_id=workspace_id, connection=connection)

    def _fail_file(
        self,
        file_id: UUID,
        code: str,
        message: str,
        *,
        job_id: int | None,
    ) -> None:
        source = metadata.tables["source_file"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(source).where(source.c.id == file_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["removed_at"] is not None:
                return
            connection.execute(
                sa.update(source)
                .where(source.c.id == file_id)
                .values(
                    status="failed",
                    error_code=code,
                    error_message=message,
                    parse_finished_at=sa.func.now(),
                    version=source.c.version + 1,
                )
            )
            connection.execute(
                sa.insert(metadata.tables["document_warning"]).values(
                    id=new_id(),
                    workspace_id=row["workspace_id"],
                    source_file_id=file_id,
                    code=code,
                    level="error",
                    message=message,
                )
            )
            _event(
                connection,
                workspace_id=row["workspace_id"],
                file_id=file_id,
                stage="parsed",
                outcome="error",
                error_code=code,
                warning_codes=(code,),
                job_id=job_id,
                detail={"message": message},
            )

    def _record_infrastructure_failure(
        self,
        file_id: UUID,
        *,
        attempt: int,
        max_attempts: int,
        error_type: str,
        job_id: int | None,
    ) -> None:
        """Make retry progress visible and end exhausted jobs in a terminal file state."""

        source = metadata.tables["source_file"]
        exhausted = attempt >= max_attempts
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(source).where(source.c.id == file_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["removed_at"] is not None:
                return
            code = "INFRASTRUCTURE_RETRIES_EXHAUSTED" if exhausted else None
            message = (
                "The file could not be processed after repeated infrastructure retries."
                if exhausted
                else None
            )
            connection.execute(
                sa.update(source)
                .where(source.c.id == file_id)
                .values(
                    status="failed" if exhausted else "queued",
                    attempt_count=source.c.attempt_count + 1,
                    error_code=code,
                    error_message=message,
                    parse_finished_at=sa.func.now() if exhausted else None,
                    version=source.c.version + 1,
                )
            )
            if exhausted:
                connection.execute(
                    sa.insert(metadata.tables["document_warning"]).values(
                        id=new_id(),
                        workspace_id=row["workspace_id"],
                        source_file_id=file_id,
                        code=cast(str, code),
                        level="error",
                        message=cast(str, message),
                        detail={"error_type": error_type},
                    )
                )
            _event(
                connection,
                workspace_id=row["workspace_id"],
                file_id=file_id,
                stage="parsed",
                outcome="error" if exhausted else "warn",
                error_code=code,
                warning_codes=(code,) if code else (),
                job_id=job_id,
                detail={
                    "infrastructure_error": error_type,
                    "attempt": attempt,
                    "exhausted": exhausted,
                },
            )

    def _refresh_parent(self, parent_id: UUID) -> None:
        source = metadata.tables["source_file"]
        with self.engine.begin() as connection:
            parent = (
                connection.execute(
                    sa.select(source).where(source.c.id == parent_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if parent is None:
                return
            children = connection.execute(
                sa.select(source.c.status, source.c.docs_found).where(
                    source.c.workspace_id == parent["workspace_id"],
                    source.c.parent_file_id == parent_id,
                )
            ).all()
            statuses = [str(row.status) for row in children]
            aggregate = _aggregate_status(statuses)
            if aggregate == "queued":
                return
            connection.execute(
                sa.update(source)
                .where(source.c.id == parent_id)
                .values(
                    status=aggregate,
                    docs_found=sum(int(row.docs_found) for row in children),
                    docs_new=0,
                    docs_updated=0,
                    docs_skipped=0,
                    parse_finished_at=sa.func.now(),
                    error_code=None if aggregate != "failed" else "ZIP_NO_SUCCESSFUL_ENTRIES",
                    error_message=None
                    if aggregate != "failed"
                    else "The archive did not contain any processable entries.",
                    version=source.c.version + 1,
                )
            )

    @requires("data.view")
    def get_file(self, workspace_id: UUID, file_id: UUID) -> dict[str, Any] | None:
        table = metadata.tables["source_file"]
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    sa.select(*table.c).where(
                        table.c.workspace_id == workspace_id,
                        table.c.id == file_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            file_row = dict(row)
            retryable_pdf_ids = self._pdf_files_without_extracted_text(connection, [file_id])
        parser_refresh_required = _parser_refresh_required(file_row)
        file_row["retry_available"] = (
            file_row["status"] in {"failed", "partial"}
            or (parser_refresh_required)
            or (
                file_row["status"] == "parsed"
                and file_row["format"] == FileFormat.PDF.value
                and file_id in retryable_pdf_ids
            )
        )
        file_row["retry_reason"] = (
            "PARSER_VERSION_OUTDATED"
            if parser_refresh_required
            else "NO_TEXT_LAYER"
            if file_id in retryable_pdf_ids
            else None
        )
        return file_row

    @requires("data.view")
    def list_files(
        self,
        workspace_id: UUID,
        *,
        batch_id: UUID | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        table = metadata.tables["source_file"]
        query = sa.select(*table.c).where(table.c.workspace_id == workspace_id)
        if batch_id is not None:
            query = query.where(table.c.batch_id == batch_id)
        query = query.order_by(table.c.uploaded_at.desc(), table.c.display_name).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
            retryable_pdf_ids = self._pdf_files_without_extracted_text(
                connection,
                [
                    cast(UUID, row["id"])
                    for row in rows
                    if row["status"] == "parsed" and row["format"] == FileFormat.PDF.value
                ],
            )
        result_rows: list[dict[str, Any]] = []
        for row in rows:
            file_row = dict(row)
            file_id = cast(UUID, row["id"])
            parser_refresh_required = _parser_refresh_required(file_row)
            file_row["retry_available"] = (
                row["status"] in {"failed", "partial"}
                or (parser_refresh_required)
                or (
                    row["status"] == "parsed"
                    and row["format"] == FileFormat.PDF.value
                    and file_id in retryable_pdf_ids
                )
            )
            file_row["retry_reason"] = (
                "PARSER_VERSION_OUTDATED"
                if parser_refresh_required
                else "NO_TEXT_LAYER"
                if file_id in retryable_pdf_ids
                else None
            )
            result_rows.append(file_row)
        return result_rows

    @staticmethod
    def _pdf_files_without_extracted_text(
        connection: Connection, file_ids: list[UUID]
    ) -> set[UUID]:
        if not file_ids:
            return set()
        version = metadata.tables["document_version"]
        document = metadata.tables["document"]
        document_text = metadata.tables["document_text"]
        statement = (
            sa.select(sa.distinct(version.c.source_file_id))
            .join(document, document.c.current_version_id == version.c.id)
            .where(
                version.c.source_file_id.in_(file_ids),
                version.c.extraction_method == ExtractionMethod.PDF_TEXT.value,
                ~sa.exists(
                    sa.select(document_text.c.document_version_id).where(
                        document_text.c.document_version_id == version.c.id,
                        document_text.c.char_count > 0,
                    )
                ),
            )
        )
        return set(connection.execute(statement).scalars())

    @requires("data.view")
    def batch_summary(self, workspace_id: UUID, batch_id: UUID) -> dict[str, Any] | None:
        batch = metadata.tables["batch"]
        source = metadata.tables["source_file"]
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    sa.select(
                        batch,
                        sa.func.count(source.c.id).label("file_count"),
                        sa.func.coalesce(sa.func.sum(source.c.docs_found), 0).label(
                            "documents_found"
                        ),
                        sa.func.coalesce(sa.func.sum(source.c.docs_new), 0).label("documents_new"),
                        sa.func.coalesce(sa.func.sum(source.c.docs_updated), 0).label(
                            "documents_updated"
                        ),
                        sa.func.coalesce(sa.func.sum(source.c.docs_skipped), 0).label(
                            "documents_skipped"
                        ),
                        sa.func.coalesce(sa.func.sum(source.c.reupload_skips), 0).label(
                            "reupload_skips"
                        ),
                    )
                    .select_from(
                        batch.outerjoin(
                            source,
                            sa.and_(
                                source.c.workspace_id == batch.c.workspace_id,
                                source.c.batch_id == batch.c.id,
                                source.c.parent_file_id.is_(None),
                            ),
                        )
                    )
                    .where(batch.c.workspace_id == workspace_id, batch.c.id == batch_id)
                    .group_by(batch.c.id)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            statuses = connection.execute(
                sa.select(source.c.status, sa.func.count(source.c.id).label("count"))
                .where(source.c.workspace_id == workspace_id, source.c.batch_id == batch_id)
                .group_by(source.c.status)
            ).all()
        result = dict(row)
        result["file_status_counts"] = {str(status): int(count) for status, count in statuses}
        return result

    @requires("data.view")
    def file_errors(self, workspace_id: UUID, file_id: UUID) -> list[dict[str, Any]] | None:
        source = metadata.tables["source_file"]
        event = metadata.tables["processing_event"]
        warning = metadata.tables["document_warning"]
        with self.engine.connect() as connection:
            exists = connection.scalar(
                sa.select(source.c.id).where(
                    source.c.workspace_id == workspace_id,
                    source.c.id == file_id,
                )
            )
            if exists is None:
                return None
            events = (
                connection.execute(
                    sa.select(
                        event.c.occurred_at,
                        event.c.stage,
                        event.c.outcome,
                        event.c.error_code,
                        event.c.warning_codes,
                        event.c.detail,
                    )
                    .where(event.c.workspace_id == workspace_id, event.c.source_file_id == file_id)
                    .order_by(event.c.occurred_at)
                )
                .mappings()
                .all()
            )
            warnings = (
                connection.execute(
                    sa.select(
                        warning.c.created_at.label("occurred_at"),
                        warning.c.code.label("error_code"),
                        warning.c.level,
                        warning.c.message,
                        warning.c.detail,
                        warning.c.resolved_at,
                    )
                    .where(
                        warning.c.workspace_id == workspace_id, warning.c.source_file_id == file_id
                    )
                    .order_by(warning.c.created_at)
                )
                .mappings()
                .all()
            )
        return [{"kind": "stage", **dict(row)} for row in events] + [
            {"kind": "warning", **dict(row)} for row in warnings
        ]

    @requires("data.view")
    def list_documents(
        self,
        workspace_id: UUID,
        *,
        doc_type: str | None = None,
        batch_id: UUID | None = None,
        source_file_id: UUID | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        document = metadata.tables["document"]
        query = sa.select(*document.c).where(
            document.c.workspace_id == workspace_id,
            document.c.withdrawn_at.is_(None),
        )
        if source_file_id is not None:
            version = metadata.tables["document_version"]
            current_file_versions = sa.select(version.c.id).where(
                version.c.workspace_id == workspace_id,
                version.c.source_file_id == source_file_id,
            )
            query = query.where(document.c.current_version_id.in_(current_file_versions))
        if doc_type:
            query = query.where(document.c.doc_type == doc_type)
        if batch_id is not None:
            query = query.where(document.c.batch_id == batch_id)
        query = query.order_by(document.c.updated_at.desc(), document.c.canonical_doc_no).limit(
            limit
        )
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [dict(row) for row in rows]

    @requires("data.view")
    def get_document(self, workspace_id: UUID, document_id: UUID) -> dict[str, Any] | None:
        document = metadata.tables["document"]
        version = metadata.tables["document_version"]
        line = metadata.tables["doc_line"]
        evidence = metadata.tables["field_evidence"]
        event = metadata.tables["processing_event"]
        with self.engine.connect() as connection:
            header = (
                connection.execute(
                    sa.select(document, version)
                    .select_from(
                        document.join(
                            version,
                            sa.and_(
                                document.c.workspace_id == version.c.workspace_id,
                                document.c.current_version_id == version.c.id,
                            ),
                        )
                    )
                    .where(
                        document.c.workspace_id == workspace_id,
                        document.c.id == document_id,
                        document.c.withdrawn_at.is_(None),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if header is None:
                return None
            version_id = cast(UUID, header["current_version_id"])
            lines = (
                connection.execute(
                    sa.select(*line.c)
                    .where(
                        line.c.workspace_id == workspace_id,
                        line.c.document_version_id == version_id,
                    )
                    .order_by(line.c.line_no)
                )
                .mappings()
                .all()
            )
            line_ids = [row["id"] for row in lines]
            evidence_query = sa.select(*evidence.c).where(
                evidence.c.workspace_id == workspace_id,
                sa.or_(
                    evidence.c.document_version_id == version_id,
                    evidence.c.doc_line_id.in_(line_ids) if line_ids else sa.false(),
                ),
            )
            evidences = connection.execute(evidence_query).mappings().all()
            ai_values = _ai_values(
                connection,
                workspace_id,
                document_id=document_id,
                version={column.name: header[column] for column in version.c},
                lines=lines,
                evidences=evidences,
            )
            events = (
                connection.execute(
                    sa.select(*event.c)
                    .where(event.c.workspace_id == workspace_id, event.c.document_id == document_id)
                    .order_by(event.c.occurred_at)
                )
                .mappings()
                .all()
            )
        return {
            "document": {column.name: header[column] for column in document.c},
            "version": {column.name: header[column] for column in version.c},
            "lines": [dict(row) for row in lines],
            "field_evidence": [dict(row) for row in evidences],
            "ai_values": ai_values,
            "processing_log": [dict(row) for row in events],
        }

    @requires("files.upload")
    def create_manual_invoice(
        self,
        actor: ActorContext,
        source_file_id: UUID,
        *,
        header_patch: Mapping[str, Any],
        lines_patch: list[Mapping[str, Any]],
        verified: bool,
        reason: str | None,
    ) -> dict[str, Any] | None:
        """Create a reviewer-entered invoice when parsing created no document."""

        source = metadata.tables["source_file"]
        version_table = metadata.tables["document_version"]
        document_table = metadata.tables["document"]
        created_document_id: UUID | None = None
        with self.engine.begin() as connection:
            file_row = (
                connection.execute(
                    sa.select(source)
                    .where(
                        source.c.workspace_id == actor.workspace_id,
                        source.c.id == source_file_id,
                        source.c.removed_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if file_row is None:
                return None
            if file_row["zone"] != UploadZone.INVOICE.value:
                raise IngestionFailure(
                    "MANUAL_INVOICE_ZONE_INVALID", "Manual invoice entry requires an invoice file."
                )
            if file_row["status"] in {"queued", "processing"}:
                raise IngestionFailure(
                    "MANUAL_INVOICE_PENDING",
                    "Wait for file processing to finish before manual entry.",
                )

            existing = (
                connection.execute(
                    sa.select(
                        version_table.c.document_id,
                        version_table.c.extraction_method,
                    )
                    .select_from(
                        version_table.join(
                            document_table,
                            sa.and_(
                                document_table.c.workspace_id == version_table.c.workspace_id,
                                document_table.c.id == version_table.c.document_id,
                            ),
                        )
                    )
                    .where(
                        version_table.c.workspace_id == actor.workspace_id,
                        version_table.c.source_file_id == source_file_id,
                        document_table.c.withdrawn_at.is_(None),
                    )
                    .order_by(version_table.c.seq.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["extraction_method"] != ExtractionMethod.MANUAL.value:
                    raise BatchVersionConflict(
                        "A document now exists for this file; edit that document."
                    )
                created_document_id = cast(UUID, existing["document_id"])
            else:

                def manual_value(value: Any) -> FieldValue[Any] | None:
                    if isinstance(value, str):
                        value = value.strip()
                    if value is None or value == "":
                        return None
                    return FieldValue(
                        value=value,
                        confidence=None,
                        source=FieldSource.MANUAL,
                        evidence=FieldEvidence(),
                        origin=FieldOrigin.MANUAL,
                        verification=FieldVerification.USER,
                        verified_by=str(actor.actor_id),
                        verified_at=datetime.now(UTC),
                    )

                def manual_header_value(field: str, value: Any) -> FieldValue[Any] | None:
                    result = manual_value(value)
                    if result is None or field not in {"po_refs", "grn_refs"}:
                        return result
                    return result.model_copy(
                        update={
                            "verification": FieldVerification.USER,
                            "verified_by": str(actor.actor_id),
                            "verified_at": datetime.now(UTC),
                        }
                    )

                header_values = {
                    field: value
                    for field, value in header_patch.items()
                    if field not in {"po_refs", "grn_refs"}
                }
                if "po_refs" in header_patch:
                    references = tuple(header_patch.get("po_refs") or ())
                    header_values["po_refs"] = references or None
                if "grn_refs" in header_patch:
                    references = tuple(header_patch.get("grn_refs") or ())
                    header_values["grn_refs"] = references or None
                if any(row.get("id") is not None for row in lines_patch):
                    raise IngestionFailure(
                        "MANUAL_INVOICE_LINE_INVALID",
                        "New manual invoice lines cannot reference an existing line.",
                    )
                line_rows = [
                    DocLine(
                        line_no=int(row["line_no"]),
                        description=manual_value(row.get("description")),
                        item_code=manual_value(row.get("item_code")),
                        hsn_sac=manual_value(row.get("hsn_sac")),
                        uom=manual_value(row.get("uom")),
                        quantity=manual_value(row.get("quantity")),
                        rate=manual_value(row.get("rate")),
                        amount=manual_value(row.get("amount")),
                    )
                    for row in lines_patch
                    if any(
                        value not in (None, "") for key, value in row.items() if key != "line_no"
                    )
                ]
                invoice_header = InvoiceHeader(
                    **{
                        field: manual_header_value(field, value)
                        for field, value in header_values.items()
                        if field not in {"doc_type", "extras"}
                    },
                    extras={},
                )
                has_invoice_content = any(
                    getattr(invoice_header, field) is not None
                    for field in (
                        "doc_no",
                        "doc_date",
                        "vendor_name",
                        "vendor_gstin",
                        "po_refs",
                        "grn_refs",
                        "taxable_total",
                        "grand_total",
                    )
                ) or bool(line_rows)
                if not has_invoice_content:
                    raise IngestionFailure(
                        "MANUAL_INVOICE_EMPTY", "Enter at least one invoice field or line item."
                    )
                file_hash = cast(str, file_row["sha256"])
                manual_document = Document(
                    header=invoice_header,
                    lines=tuple(line_rows),
                    natural_key=_canonical_hash(
                        {"manual_source_file_id": str(source_file_id), "source_sha256": file_hash}
                    ),
                    source_locator="manual_invoice_entry",
                    needs_verification=not verified,
                )
                hints = self._parser_hints(actor.workspace_id, file_row)
                outcome, created_document_id, version_id = self._upsert_document(
                    connection,
                    workspace_id=actor.workspace_id,
                    batch_id=cast(UUID | None, file_row["batch_id"]),
                    file_id=source_file_id,
                    actor_id=actor.actor_id,
                    document=manual_document,
                    file_sha=file_hash,
                    job_id=None,
                    parser_version=PARSER_VERSION,
                    layout_signature=cast(
                        str,
                        file_row["layout_signature"]
                        or _canonical_hash({"manual_invoice_file": str(source_file_id)}),
                    ),
                    extraction_method=ExtractionMethod.MANUAL,
                    uom_aliases=hints.uom_aliases,
                    charge_keywords=hints.charge_keywords,
                )
                if outcome != "new":
                    raise BatchVersionConflict("A document already exists for this file.")
                connection.execute(
                    sa.update(source)
                    .where(
                        source.c.workspace_id == actor.workspace_id,
                        source.c.id == source_file_id,
                    )
                    .values(
                        status="parsed",
                        error_code=None,
                        error_message=None,
                        parser_version=PARSER_VERSION,
                        docs_found=1,
                        docs_new=1,
                        docs_updated=0,
                        docs_skipped=0,
                        parse_finished_at=sa.func.now(),
                        version=source.c.version + 1,
                    )
                )
                _event(
                    connection,
                    workspace_id=actor.workspace_id,
                    file_id=source_file_id,
                    document_id=created_document_id,
                    document_version_id=version_id,
                    stage="upserted",
                    outcome="ok" if verified else "warn",
                    parser_version=PARSER_VERSION,
                    detail={
                        "source": "manual",
                        "verified": verified,
                        "reason": reason,
                    },
                )
                write_audit_event(
                    connection,
                    actor,
                    action="document.manual_create",
                    target_type="document",
                    target_id=created_document_id,
                    before_state={
                        "source_file_id": str(source_file_id),
                        "status": file_row["status"],
                    },
                    after_state={
                        "doc_type": DocType.INVOICE.value,
                        "extraction_method": ExtractionMethod.MANUAL.value,
                        "needs_verification": not verified,
                    },
                    reason=reason
                    or "Reviewer entered invoice fields because parsing created no record.",
                )
                self.queue.enqueue("resolve_vendors", file_id=source_file_id, connection=connection)

        if created_document_id is not None and file_row["parent_file_id"] is not None:
            self._refresh_parent(cast(UUID, file_row["parent_file_id"]))
        return (
            self.get_document(actor.workspace_id, created_document_id)
            if created_document_id
            else None
        )

    @requires("files.upload")
    def correct_invoice_document(
        self,
        actor: ActorContext,
        document_id: UUID,
        *,
        expected_version: int,
        header_patch: Mapping[str, Any],
        lines_patch: list[Mapping[str, Any]] | None,
        verified: bool,
        reason: str | None,
        ai_suggestion_refs: Sequence[AiSuggestionRef] = (),
    ) -> dict[str, Any] | None:
        """Append a human-reviewed document version and preserve correction evidence.

        Invoice saves run deterministic arithmetic QA (TR-PAR-5); a failure is
        flagged as a warning. Values taken from grounded suggestions must still
        match the current suggestion; their evidence keeps its quote, page and
        ``ai_call_id``.
        """

        document_table = metadata.tables["document"]
        version_table = metadata.tables["document_version"]
        line_table = metadata.tables["doc_line"]
        evidence_table = metadata.tables["field_evidence"]
        document_text_table = metadata.tables["document_text"]
        with self.engine.begin() as connection:
            current_document = (
                connection.execute(
                    sa.select(document_table)
                    .where(
                        document_table.c.workspace_id == actor.workspace_id,
                        document_table.c.id == document_id,
                        document_table.c.withdrawn_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if current_document is None:
                return None
            if int(current_document["version"]) != expected_version:
                raise BatchVersionConflict("The document changed after it was loaded.")
            doc_type = str(current_document["doc_type"])
            header_columns = _correction_header_columns(doc_type)
            line_columns = _correction_line_columns(doc_type)
            if not header_columns or not line_columns:
                raise IngestionFailure(
                    "MANUAL_CORRECTION_UNSUPPORTED",
                    "Manual field correction is not enabled for this document type.",
                )
            linked_po_ids: set[UUID] = set()
            if doc_type == DocType.GRN.value:
                link_current = metadata.tables["link_current"]
                link_table = metadata.tables["link"]
                linked_po_ids = {
                    cast(UUID, linked_po_id)
                    for linked_po_id in connection.scalars(
                        sa.select(link_current.c.to_document_id)
                        .select_from(
                            link_current.join(
                                link_table,
                                sa.and_(
                                    link_current.c.workspace_id == link_table.c.workspace_id,
                                    link_current.c.current_link_id == link_table.c.id,
                                ),
                            )
                        )
                        .where(
                            link_current.c.workspace_id == actor.workspace_id,
                            link_current.c.from_document_id == document_id,
                            link_current.c.link_type == "grn_po",
                            link_table.c.state.in_(("auto", "suggested", "confirmed")),
                            sa.not_(
                                sa.and_(
                                    link_table.c.ai_suggested,
                                    link_table.c.state == "suggested",
                                )
                            ),
                        )
                    ).all()
                }
            previous_version_id = cast(UUID, current_document["current_version_id"])
            current_version = (
                connection.execute(
                    sa.select(version_table)
                    .where(
                        version_table.c.workspace_id == actor.workspace_id,
                        version_table.c.id == previous_version_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            current_lines = (
                connection.execute(
                    sa.select(*line_table.c)
                    .where(
                        line_table.c.workspace_id == actor.workspace_id,
                        line_table.c.document_version_id == previous_version_id,
                    )
                    .order_by(line_table.c.line_no)
                )
                .mappings()
                .all()
            )
            old_line_by_id = {cast(UUID, row["id"]): dict(row) for row in current_lines}
            current_line_ids = list(old_line_by_id)
            old_evidence = (
                connection.execute(
                    sa.select(*evidence_table.c)
                    .where(
                        evidence_table.c.workspace_id == actor.workspace_id,
                        sa.or_(
                            evidence_table.c.document_version_id == previous_version_id,
                            evidence_table.c.doc_line_id.in_(current_line_ids)
                            if current_line_ids
                            else sa.false(),
                        ),
                    )
                    # Oldest first, so each value's latest row wins below.
                    .order_by(evidence_table.c.created_at, evidence_table.c.id)
                )
                .mappings()
                .all()
            )
            old_text_pages = (
                connection.execute(
                    sa.select(*document_text_table.c).where(
                        document_text_table.c.workspace_id == actor.workspace_id,
                        document_text_table.c.document_version_id == previous_version_id,
                    )
                )
                .mappings()
                .all()
            )

            new_version = dict(current_version)
            new_version_id = new_id()
            latest_seq = connection.scalar(
                sa.select(sa.func.max(version_table.c.seq)).where(
                    version_table.c.workspace_id == actor.workspace_id,
                    version_table.c.document_id == document_id,
                )
            )
            new_version.update(
                id=new_version_id,
                seq=int(latest_seq or 0) + 1,
                derived_from_version_id=previous_version_id,
                created_by=actor.actor_id,
                extraction_method=ExtractionMethod.MANUAL.value,
            )
            new_version.pop("created_at", None)

            normalized_header_patch: dict[str, Any] = {}
            header_changes: dict[str, tuple[Any, Any]] = {}
            for field_name, raw_value in header_patch.items():
                column_name = header_columns.get(field_name)
                if column_name is None:
                    raise IngestionFailure(
                        "MANUAL_CORRECTION_FIELD_INVALID",
                        "A field is not editable for this document type.",
                    )
                if field_name in {"po_refs", "grn_refs"}:
                    if raw_value is None:
                        value: Any = []
                    elif isinstance(raw_value, (tuple, list)):
                        value = [str(item).strip() for item in raw_value if str(item).strip()]
                        if len(value) > 50 or any(len(item) > 128 for item in value):
                            raise IngestionFailure(
                                "MANUAL_CORRECTION_VALUE_INVALID",
                                "A reference is too long or there are too many references.",
                            )
                    else:
                        raise IngestionFailure(
                            "MANUAL_CORRECTION_VALUE_INVALID",
                            "PO and GRN references must be sent as a list.",
                        )
                elif isinstance(raw_value, str):
                    value = raw_value.strip() or None
                    if value is not None and len(value) > 1_000:
                        raise IngestionFailure(
                            "MANUAL_CORRECTION_VALUE_INVALID", "A document value is too long."
                        )
                    if field_name == "vendor_gstin" and value is not None:
                        value = value.upper().replace(" ", "")
                    if field_name == "currency" and value is not None:
                        value = value.upper()
                else:
                    value = raw_value
                previous_value = current_version[column_name]
                if field_name == "po_refs":
                    previous_value = list(current_version["po_refs"] or [])
                elif field_name == "grn_refs":
                    previous_value = list(current_version["grn_refs"] or [])
                if previous_value != value:
                    header_changes[field_name] = (previous_value, value)
                normalized_header_patch[field_name] = value
                new_version[column_name] = value

            vendor_gstin = new_version.get("vendor_gstin")
            if "vendor_gstin" in normalized_header_patch:
                vendor_gstin = vendor_gstin.upper().replace(" ", "") if vendor_gstin else None
                new_version["vendor_gstin"] = vendor_gstin
                new_version["vendor_pan"] = (
                    vendor_gstin[2:12] if vendor_gstin and len(vendor_gstin) == 15 else None
                )
                new_version["vendor_state_code"] = (
                    vendor_gstin[:2] if vendor_gstin and len(vendor_gstin) == 15 else None
                )
            if "vendor_name" in normalized_header_patch:
                name = new_version.get("vendor_name")
                new_version["vendor_name_norm"] = normalize_vendor_name(name) if name else None
            if "doc_no" in normalized_header_patch:
                doc_no = new_version.get("doc_no")
                new_version["canonical_doc_no"] = (
                    canonicalize_document_number(doc_no) if doc_no else None
                )
            if "po_refs" in normalized_header_patch:
                new_version["canonical_po_refs"] = [
                    canonicalize_document_number(ref) for ref in new_version["po_refs"]
                ]
            if "grn_refs" in normalized_header_patch:
                new_version["canonical_grn_refs"] = [
                    canonicalize_document_number(ref) for ref in new_version["grn_refs"]
                ]
            if "challan_no" in normalized_header_patch:
                new_version["canonical_challan_no"] = _canonical_optional(
                    new_version.get("challan_no")
                )
            if "supplier_invoice_no" in normalized_header_patch:
                new_version["canonical_supplier_invoice_no"] = _canonical_optional(
                    new_version.get("supplier_invoice_no")
                )
            if "revision" in normalized_header_patch:
                new_version["revision_rank"] = _revision_rank(new_version.get("revision"))
            if "payment_terms" in normalized_header_patch:
                new_version["payment_terms_days"] = _payment_days(
                    new_version.get("payment_terms_text")
                )

            old_lines_by_id = old_line_by_id
            requested_lines = None if lines_patch is None else list(lines_patch)
            if requested_lines is None:
                requested_lines = [
                    {"id": row["id"], "line_no": row["line_no"]} for row in current_lines
                ]
            seen_old_ids: set[UUID] = set()
            seen_line_numbers: set[int] = set()
            new_lines: list[dict[str, Any]] = []
            new_line_ids: dict[UUID, UUID] = {}
            changed_line_fields: dict[UUID, set[str]] = {}
            changed_line_values: dict[tuple[UUID, str], Any] = {}
            line_no_by_owner: dict[UUID, int] = {}
            shown_line_fields: dict[UUID, set[str]] = {}
            for supplied in requested_lines:
                unknown_fields = set(supplied) - {"id", "line_no", *line_columns}
                if unknown_fields:
                    raise IngestionFailure(
                        "MANUAL_CORRECTION_FIELD_INVALID",
                        "A line field is not editable for this document type.",
                    )
                old_id = supplied.get("id")
                old_row: dict[str, Any] | None = None
                if old_id is not None:
                    old_uuid = old_id if isinstance(old_id, UUID) else UUID(str(old_id))
                    old_row = old_lines_by_id.get(old_uuid)
                    if old_row is None or old_uuid in seen_old_ids:
                        raise IngestionFailure(
                            "MANUAL_CORRECTION_LINE_INVALID",
                            "A line does not belong to this document or is duplicated.",
                        )
                    seen_old_ids.add(old_uuid)
                line_no = int(supplied["line_no"])
                if line_no < 1 or line_no in seen_line_numbers:
                    raise IngestionFailure(
                        "MANUAL_CORRECTION_LINE_INVALID", "Document line numbers must be unique."
                    )
                seen_line_numbers.add(line_no)
                row = dict(old_row or {})
                new_line_id = new_id()
                row.update(
                    id=new_line_id,
                    workspace_id=actor.workspace_id,
                    document_version_id=new_version_id,
                    line_no=line_no,
                )
                row.setdefault("line_type", "goods")
                row.setdefault("extras", {})
                row.setdefault("tokens", [])
                row.setdefault("spec_tokens", [])
                change_owner_id = cast(UUID, old_row["id"]) if old_row is not None else new_line_id
                line_no_by_owner[change_owner_id] = line_no
                shown_line_fields[change_owner_id] = set(supplied) & set(line_columns)
                for field_name, column_name in line_columns.items():
                    if field_name not in supplied:
                        continue
                    raw_value = supplied[field_name]
                    value = raw_value.strip() or None if isinstance(raw_value, str) else raw_value
                    if isinstance(value, str) and len(value) > 2_000:
                        raise IngestionFailure(
                            "MANUAL_CORRECTION_VALUE_INVALID", "An invoice line value is too long."
                        )
                    previous_value = old_row.get(column_name) if old_row else None
                    if field_name == "uom":
                        previous_value = old_row.get("uom_as_read") if old_row else None
                    if previous_value != value:
                        changed_line_fields.setdefault(change_owner_id, set()).add(field_name)
                        changed_line_values[(change_owner_id, field_name)] = value
                    if field_name == "uom":
                        row["uom_as_read"] = value
                        unit = normalize_uom(value, {}) if value else None
                        row["uom"] = unit.canonical if unit and unit.is_known else None
                    else:
                        row[column_name] = value
                    if field_name == "po_ref":
                        row["canonical_po_ref"] = _canonical_optional(value)
                description = row.get("description")
                normalized = normalize_item_text(description) if description else None
                row["description_norm"] = normalized.description if normalized else None
                row["tokens"] = list(normalized.tokens) if normalized else []
                row["spec_tokens"] = list(normalized.spec_tokens) if normalized else []
                new_lines.append(row)
                if old_row is not None:
                    new_line_ids[cast(UUID, old_row["id"])] = new_line_id
                else:
                    new_line_ids[new_line_id] = new_line_id

            removed_line_ids = (
                set(old_lines_by_id) - seen_old_ids if lines_patch is not None else set()
            )

            changed_header_fields = set(header_changes)
            any_correction = bool(changed_header_fields or changed_line_fields or removed_line_ids)
            should_verify = verified
            needs_verification = (
                not should_verify
                if any_correction or verified
                else bool(current_version["needs_verification"])
            )
            if verified and not any_correction and not current_version["needs_verification"]:
                needs_verification = False
            new_version["needs_verification"] = needs_verification
            new_version["verification_reasons"] = (
                []
                if not needs_verification
                else list(current_version["verification_reasons"] or [])
            )
            extras = dict(new_version.get("extras") or {})
            extras["manual_review"] = {
                "actor_id": str(actor.actor_id),
                "verified": not needs_verification,
                "reason": reason,
            }
            qa = (
                _correction_qa(new_version, new_lines)
                if doc_type == DocType.INVOICE.value
                else None
            )
            if qa is not None:
                extras["correction_qa"] = qa
            new_version["extras"] = extras
            ai_evidence = self._ai_reference_evidence(
                connection,
                actor.workspace_id,
                current_document,
                ai_suggestion_refs,
                new_version=new_version,
                new_lines=new_lines,
            )

            def manual_evidence(key: tuple[str, int | None, str]) -> dict[str, Any]:
                grounded = ai_evidence.get(key)
                verified_values = {
                    "source": "manual",
                    "origin": FieldOrigin.MANUAL.value,
                    "confidence": None,
                    "verification": FieldVerification.USER.value,
                    "verified_by": str(actor.actor_id),
                    "verified_at": sa.func.now(),
                }
                if grounded is None:
                    return {
                        **verified_values,
                        "page": None,
                        "bbox": None,
                        "snippet": _MANUAL_SNIPPET,
                        "ai_call_id": None,
                    }
                ai_call_id, suggestion = grounded
                return {
                    **verified_values,
                    "page": suggestion.page,
                    "bbox": list(suggestion.bbox) if suggestion.bbox is not None else None,
                    "snippet": suggestion.quote[:_AI_SNIPPET_LIMIT],
                    "ai_call_id": ai_call_id,
                }

            version_hash_payload = {
                "header": {
                    key: value
                    for key, value in new_version.items()
                    if key
                    not in {
                        "id",
                        "workspace_id",
                        "document_id",
                        "seq",
                        "content_hash",
                        "derived_from_version_id",
                        "created_by",
                        "created_at",
                    }
                },
                "lines": [
                    {
                        key: value
                        for key, value in row.items()
                        if key not in {"id", "workspace_id", "document_version_id"}
                    }
                    for row in new_lines
                ],
            }
            new_version["content_hash"] = _canonical_hash(version_hash_payload)
            connection.execute(sa.insert(version_table).values(**new_version))
            for old_page in old_text_pages:
                page = dict(old_page)
                page["document_version_id"] = new_version_id
                page.pop("created_at", None)
                connection.execute(sa.insert(document_text_table).values(**page))
            for row in new_lines:
                connection.execute(sa.insert(line_table).values(**row))

            manual_evidence_done: set[tuple[str, UUID | None, str]] = set()
            header_column_by_field = header_columns
            is_invoice = doc_type == DocType.INVOICE.value
            # Unchanged values that marking the document reviewed verified (P2, PD-2b).
            verified_fields: set[str] = set()

            def verify(item: dict[str, Any], name: str) -> None:
                item.update(
                    verification=FieldVerification.USER.value,
                    verified_by=str(actor.actor_id),
                    verified_at=sa.func.now(),
                )
                verified_fields.add(name)

            # Only each value's latest row moves to the new version. The copies share one
            # time and their ids are not ordered, so an older row copied as well could hide a
            # later verification (PD-2b).
            latest_evidence = {
                (row["document_version_id"], row["doc_line_id"], row["field_name"]): row
                for row in old_evidence
            }
            for old_item in latest_evidence.values():
                item = dict(old_item)
                old_line_id = cast(UUID | None, item["doc_line_id"])
                if old_line_id is not None:
                    new_line_id = new_line_ids.get(old_line_id)
                    if new_line_id is None:
                        continue
                    item["doc_line_id"] = new_line_id
                    item["document_version_id"] = None
                    changed = item["field_name"] in changed_line_fields.get(old_line_id, set())
                    if changed:
                        field_name = str(item["field_name"])
                        raw_value = changed_line_values.get((old_line_id, field_name))
                        item.update(
                            value_text=_field_text(raw_value) if raw_value is not None else None,
                            **manual_evidence(
                                ("line", line_no_by_owner.get(old_line_id), field_name)
                            ),
                        )
                        manual_evidence_done.add(("line", old_line_id, field_name))
                    elif verified and _verified_by_review(
                        item,
                        references=("po_ref",),
                        shown_key_fields=(
                            _REVIEWED_LINE_FIELDS & shown_line_fields.get(old_line_id, set())
                            if is_invoice
                            else frozenset()
                        ),
                    ):
                        verify(
                            item,
                            f"line {line_no_by_owner.get(old_line_id)} {item['field_name']}",
                        )
                else:
                    item["document_version_id"] = new_version_id
                    changed = item["field_name"] in changed_header_fields
                    if changed:
                        field_name = str(item["field_name"])
                        column_name = header_column_by_field[field_name]
                        raw_value = new_version[column_name]
                        item.update(
                            value_text=_field_text(raw_value) if raw_value else None,
                            **manual_evidence(("header", None, field_name)),
                        )
                        manual_evidence_done.add(("header", None, field_name))
                    elif verified and _verified_by_review(
                        item,
                        references=("po_refs", "grn_refs"),
                        shown_key_fields=(
                            _REVIEWED_HEADER_FIELDS & set(header_patch)
                            if is_invoice
                            else frozenset()
                        ),
                    ):
                        verify(item, str(item["field_name"]))
                item["id"] = new_id()
                item.pop("created_at", None)
                connection.execute(sa.insert(evidence_table).values(**item))

            for field_name in changed_header_fields:
                if ("header", None, field_name) in manual_evidence_done:
                    continue
                raw_value = new_version[header_column_by_field[field_name]]
                connection.execute(
                    sa.insert(evidence_table).values(
                        id=new_id(),
                        workspace_id=actor.workspace_id,
                        document_version_id=new_version_id,
                        field_name=field_name,
                        value_text=_field_text(raw_value) if raw_value else None,
                        **manual_evidence(("header", None, field_name)),
                    )
                )
            changed_line_evidence = {
                (old_id, field_name)
                for old_id, fields in changed_line_fields.items()
                if old_id is not None
                for field_name in fields
            }
            existing_manual_evidence = {
                (old_id, field_name)
                for kind, old_id, field_name in manual_evidence_done
                if kind == "line" and old_id is not None
            }
            for old_id, field_name in changed_line_evidence - existing_manual_evidence:
                target_id = new_line_ids.get(old_id)
                if target_id is None:
                    continue
                value = changed_line_values.get((old_id, field_name))
                connection.execute(
                    sa.insert(evidence_table).values(
                        id=new_id(),
                        workspace_id=actor.workspace_id,
                        doc_line_id=target_id,
                        field_name=field_name,
                        value_text=_field_text(value) if value is not None else None,
                        **manual_evidence(("line", line_no_by_owner.get(old_id), field_name)),
                    )
                )

            new_doc_no = new_version.get("doc_no")
            new_doc_date = new_version.get("doc_date")
            new_vendor_identity = _vendor_identity(
                cast(str | None, new_version.get("vendor_gstin")),
                cast(str | None, new_version.get("vendor_name")),
            )
            connection.execute(
                sa.update(document_table)
                .where(
                    document_table.c.workspace_id == actor.workspace_id,
                    document_table.c.id == document_id,
                )
                .values(
                    current_version_id=new_version_id,
                    vendor_identity=new_vendor_identity,
                    doc_no=new_doc_no,
                    canonical_doc_no=(
                        canonicalize_document_number(new_doc_no) if new_doc_no else None
                    ),
                    doc_date=new_doc_date,
                    grand_total=new_version.get("grand_total"),
                    needs_verification=needs_verification,
                    input_seq=document_table.c.input_seq
                    + (1 if doc_type == DocType.PO.value and (any_correction or verified) else 0),
                    updated_at=sa.func.now(),
                    version=document_table.c.version + 1,
                )
            )
            write_audit_event(
                connection,
                actor,
                action="document.correct",
                target_type="document",
                target_id=document_id,
                before_state={
                    "document_version_id": str(previous_version_id),
                    "fields": {key: before for key, (before, _after) in header_changes.items()},
                },
                after_state={
                    "document_version_id": str(new_version_id),
                    "fields": {key: after for key, (_before, after) in header_changes.items()},
                    "corrected_line_count": len(changed_line_fields) + len(removed_line_ids),
                    "removed_line_ids": sorted(str(value) for value in removed_line_ids),
                    "needs_verification": needs_verification,
                    "verified_fields": sorted(verified_fields),
                    "correction_qa": qa,
                    "ai_suggestion_refs": [
                        {
                            "ai_call_id": str(ai_call_id),
                            "scope": scope,
                            "line_no": line_no,
                            "field": field_name,
                        }
                        for (scope, line_no, field_name), (ai_call_id, _item) in sorted(
                            ai_evidence.items(), key=lambda item: str(item[0])
                        )
                    ],
                },
                reason=reason or f"{doc_type.upper()} extraction reviewed by user.",
            )
            # Any changed correction can alter link inputs even when vendor identity stays put.
            if any_correction:
                source_file_id = current_version["source_file_id"]
                if source_file_id is not None:
                    self.queue.enqueue(
                        "resolve_vendors",
                        file_id=cast(UUID, source_file_id),
                        connection=connection,
                    )
            _event(
                connection,
                workspace_id=actor.workspace_id,
                file_id=cast(UUID, current_version["source_file_id"]),
                document_id=document_id,
                document_version_id=new_version_id,
                stage="qa",
                outcome="warn" if qa is not None and not qa["passed"] else "ok",
                parser_version=PARSER_VERSION,
                warning_codes=(
                    (_CORRECTION_QA_CODE,) if qa is not None and not qa["passed"] else ()
                ),
                detail={
                    "changed_header_fields": sorted(header_changes),
                    "changed_line_count": len(changed_line_fields),
                    "verified": not needs_verification,
                    "correction_qa": qa,
                },
            )
            if qa is not None:
                self._record_correction_qa(
                    connection,
                    actor,
                    document_id=document_id,
                    version_id=new_version_id,
                    file_id=cast(UUID | None, current_version["source_file_id"]),
                    qa=qa,
                )
            if doc_type == DocType.PO.value:
                self._mark_dirty_scope(
                    connection,
                    workspace_id=actor.workspace_id,
                    scope_kind="po",
                    scope_id=document_id,
                    reason="purchase order extraction corrected or verified by user",
                )
            elif doc_type == DocType.GRN.value:
                for po_document_id in sorted(linked_po_ids, key=str):
                    self._mark_dirty_scope(
                        connection,
                        workspace_id=actor.workspace_id,
                        scope_kind="po",
                        scope_id=po_document_id,
                        reason="goods receipt extraction corrected or verified by user",
                    )
            else:
                self._mark_dirty_scope(
                    connection,
                    workspace_id=actor.workspace_id,
                    scope_kind="invoice",
                    scope_id=document_id,
                    reason="invoice extraction corrected or verified by user",
                )
        return self.get_document(actor.workspace_id, document_id)

    @requires("files.upload")
    def verify_ai_values(
        self,
        actor: ActorContext,
        document_id: UUID,
        *,
        expected_version: int,
        values: Sequence[tuple[UUID | None, str]] = (),
        all_agreeing: bool = False,
    ) -> dict[str, Any] | None:
        """Record that a person verified values read by AI (PD-4d).

        Each value gets one new ``user``-verified evidence row; the value itself and the
        document version stay as they are. "Verify all" covers only the values whose OCR
        cross-check agrees (Q8); the others need their own Verify.
        """

        document_table = metadata.tables["document"]
        version_table = metadata.tables["document_version"]
        line_table = metadata.tables["doc_line"]
        evidence_table = metadata.tables["field_evidence"]
        with self.engine.begin() as connection:
            current_document = (
                connection.execute(
                    sa.select(document_table)
                    .where(
                        document_table.c.workspace_id == actor.workspace_id,
                        document_table.c.id == document_id,
                        document_table.c.withdrawn_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if current_document is None:
                return None
            if int(current_document["version"]) != expected_version:
                raise BatchVersionConflict("The document changed after it was loaded.")
            if current_document["doc_type"] != DocType.INVOICE.value:
                raise IngestionFailure(
                    "AI_VERIFY_UNSUPPORTED", "Values read by AI are verified on invoices only."
                )
            version_id = cast(UUID, current_document["current_version_id"])
            version = (
                connection.execute(
                    sa.select(version_table).where(
                        version_table.c.workspace_id == actor.workspace_id,
                        version_table.c.id == version_id,
                    )
                )
                .mappings()
                .one()
            )
            lines = (
                connection.execute(
                    sa.select(line_table.c.id, line_table.c.line_no).where(
                        line_table.c.workspace_id == actor.workspace_id,
                        line_table.c.document_version_id == version_id,
                    )
                )
                .mappings()
                .all()
            )
            line_ids = [row["id"] for row in lines]
            evidences = (
                connection.execute(
                    sa.select(*evidence_table.c).where(
                        evidence_table.c.workspace_id == actor.workspace_id,
                        sa.or_(
                            evidence_table.c.document_version_id == version_id,
                            evidence_table.c.doc_line_id.in_(line_ids) if line_ids else sa.false(),
                        ),
                    )
                )
                .mappings()
                .all()
            )
            waiting = {
                (item["line_id"], item["field_name"]): item
                for item in _ai_values(
                    connection,
                    actor.workspace_id,
                    document_id=document_id,
                    version=version,
                    lines=lines,
                    evidences=evidences,
                )
                if not item["verified"]
            }
            if all_agreeing:
                chosen = [item for item in waiting.values() if item["agrees"]]
            else:
                chosen = []
                for key in dict.fromkeys(values):
                    if key not in waiting:
                        raise IngestionFailure(
                            "AI_VALUE_NOT_WAITING",
                            "A chosen value is not a value read by AI that waits for a person.",
                        )
                    chosen.append(waiting[key])
            if not chosen:
                raise IngestionFailure(
                    "AI_VALUE_NONE_TO_VERIFY",
                    "No value read by AI waits for verification here."
                    if not all_agreeing
                    else "No value read by AI agrees with its OCR cross-check.",
                )
            latest = _latest_evidence(evidences)
            for item in chosen:
                owner = (version_id, None) if item["line_id"] is None else (None, item["line_id"])
                row = dict(latest[(*owner, item["field_name"])])
                row.update(
                    id=new_id(),
                    verification=FieldVerification.USER.value,
                    verified_by=str(actor.actor_id),
                    verified_at=sa.func.now(),
                )
                row.pop("created_at", None)
                connection.execute(sa.insert(evidence_table).values(**row))
            connection.execute(
                sa.update(document_table)
                .where(
                    document_table.c.workspace_id == actor.workspace_id,
                    document_table.c.id == document_id,
                )
                .values(
                    input_seq=document_table.c.input_seq + 1,
                    updated_at=sa.func.now(),
                    version=document_table.c.version + 1,
                )
            )
            write_audit_event(
                connection,
                actor,
                action="document.verify_ai_values",
                target_type="document",
                target_id=document_id,
                before_state={"document_version_id": str(version_id)},
                after_state={
                    "document_version_id": str(version_id),
                    "verified_fields": sorted(str(item["label"]) for item in chosen),
                    "verify_all_agreeing": all_agreeing,
                },
                reason="Values read by AI verified by user.",
            )
            self._mark_dirty_scope(
                connection,
                workspace_id=actor.workspace_id,
                scope_kind="invoice",
                scope_id=document_id,
                reason="values read by AI verified by user",
            )
        return self.get_document(actor.workspace_id, document_id)

    def _record_correction_qa(
        self,
        connection: Connection,
        actor: ActorContext,
        *,
        document_id: UUID,
        version_id: UUID,
        file_id: UUID | None,
        qa: Mapping[str, Any],
    ) -> None:
        """Open a warning when correction QA fails; resolve it once a save passes."""

        table = metadata.tables["document_warning"]
        if qa["passed"]:
            connection.execute(
                sa.update(table)
                .where(
                    table.c.workspace_id == actor.workspace_id,
                    table.c.document_id == document_id,
                    table.c.code == _CORRECTION_QA_CODE,
                    table.c.resolved_at.is_(None),
                )
                .values(resolved_at=sa.func.now(), resolved_by=actor.actor_id)
            )
            return
        if file_id is None:
            return
        self._write_warnings(
            connection,
            workspace_id=actor.workspace_id,
            file_id=file_id,
            document_id=document_id,
            warning_rows=[
                ParseWarning(
                    code=_CORRECTION_QA_CODE,
                    message=(
                        "Corrected values do not reconcile arithmetically: "
                        + ", ".join(qa["failures"])
                        + "."
                    ),
                )
            ],
            document=None,
            document_version_id=version_id,
        )

    def _h1_review(
        self, connection: Connection, workspace_id: UUID, document: Mapping[Any, Any]
    ) -> tuple[dict[str, Any], tuple[GroundedSuggestion, ...]]:
        """The latest H1 or vision answer for this document, checked again before use.

        H1 answers are re-grounded on the current page text; vision answers (AC-5)
        are re-checked against the pages that were read, the current buyer
        entities and the current values. PO and GRN documents get vision readings
        only; their corrections use the same versioned manual-evidence path.
        """

        requests = metadata.tables["ai_request"]
        versions = metadata.tables["document_version"]
        cache = metadata.tables["ai_cache"]
        call_log = metadata.tables["ai_call_log"]
        current_version_id = cast(UUID, document["current_version_id"])
        view: dict[str, Any] = {
            "document_id": document["id"],
            "document_version": int(document["version"]),
            "current_version_id": current_version_id,
            "state": "not_applicable",
            "code": None,
            "ai_call_id": None,
            "mode": None,
            "label": None,
            "provider": None,
            "model": None,
            "requested_at": None,
            "suggestions": [],
            "dropped": {},
            "source": None,
            "correctable": document["doc_type"] in vision.CORRECTABLE_DOC_TYPES,
            "annotations": [],
        }
        if document["doc_type"] not in vision.WIRED_DOC_TYPES:
            return view, ()
        hooks = [vision.HOOK]
        if document["doc_type"] in h1.WIRED_DOC_TYPES:
            hooks.append(h1.HOOK)
        of_document = (
            requests.c.workspace_id == workspace_id,
            requests.c.hook.in_(hooks),
            requests.c.subject_type == h1.SUBJECT_TYPE,
            requests.c.subject_id.in_(
                sa.select(versions.c.id).where(
                    versions.c.workspace_id == workspace_id,
                    versions.c.document_id == document["id"],
                )
            ),
        )
        # A shadow reading (WI-BE6) is never shown; only H1 or the workspace's reader is.
        request = (
            connection.execute(
                sa.select(requests)
                .where(*of_document, requests.c.shadow.is_(False))
                .order_by(requests.c.created_at.desc(), requests.c.attempt_no.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if request is None:
            shadowed = connection.scalar(
                sa.select(sa.exists().where(*of_document, requests.c.shadow.is_(True)))
            )
            if shadowed:
                view["code"] = shadow.SHADOW_CODE
            return view, ()
        is_vision = request["hook"] == vision.HOOK
        view.update(
            source="vision" if is_vision else "text",
            mode=request["mode"],
            label=(vision.SUGGESTION_LABELS if is_vision else h1.SUGGESTION_LABELS).get(
                str(request["mode"])
            ),
            provider=request["provider"],
            model=request["model"],
            requested_at=request["created_at"],
        )
        state = str(request["request_state"])
        if state in {"queued", "reserved", "sending"}:
            view["state"] = "pending"
            return view, ()
        if state != "succeeded" or request["apply_outcome"] not in {"applied", "not_applicable"}:
            view.update(
                state="unavailable",
                code=request["error_code"]
                or request["skip_code"]
                or (request["apply_outcome"] if state == "succeeded" else state),
            )
            return view, ()
        ttl_days = (self.ai_limits().limits or AiLimits()).cache_ttl_days
        response = connection.scalar(
            sa.select(cache.c.response).where(
                cache.c.workspace_id == workspace_id,
                cache.c.cache_key == request["cache_key"],
                cache.c.created_at > datetime.now(UTC) - timedelta(days=ttl_days),
            )
        )
        if request["cache_key"] is None or not isinstance(response, Mapping):
            view["state"] = "expired"
            return view, ()
        view["ai_call_id"] = connection.scalar(
            sa.select(call_log.c.id)
            .where(
                call_log.c.workspace_id == workspace_id,
                call_log.c.ai_request_id == request["id"],
            )
            .order_by(call_log.c.created_at.desc())
            .limit(1)
        )
        if is_vision:
            return self._vision_review(
                connection, workspace_id, document, request, dict(response), view
            )
        pages = h1.redacted_pages(h1.load_pages(connection, workspace_id, current_version_id))
        try:
            validation = validate_h1(
                H1Output.model_validate(dict(response)),
                doc_type=document["doc_type"],
                pages=pages,
            )
        except (InvalidOutput, ValidationError) as exc:
            code = exc.code if isinstance(exc, InvalidOutput) else "invalid_output"
            view.update(state="unavailable", code=code)
            return view, ()
        version_row = (
            connection.execute(
                sa.select(versions).where(
                    versions.c.workspace_id == workspace_id, versions.c.id == current_version_id
                )
            )
            .mappings()
            .one()
        )
        line_table = metadata.tables["doc_line"]
        lines_by_no = {
            int(row["line_no"]): row
            for row in connection.execute(
                sa.select(line_table).where(
                    line_table.c.workspace_id == workspace_id,
                    line_table.c.document_version_id == current_version_id,
                )
            ).mappings()
        }
        suggestions = []
        for item in validation.accepted:
            if item.scope == "header":
                current = version_row[INVOICE_HEADER_COLUMNS[item.field]]
            else:
                line = lines_by_no.get(cast(int, item.line_no))
                current = line[INVOICE_LINE_COLUMNS[item.field]] if line is not None else None
            suggestions.append(
                {
                    "scope": item.scope,
                    "line_no": item.line_no,
                    "field": item.field,
                    "value": item.value,
                    "current_value": current,
                    "matches_current": _suggestion_matches(item, current),
                    "page": item.page,
                    "quote": item.quote,
                    "start": item.original_start,
                    "end": item.original_end,
                }
            )
        view.update(state="available", suggestions=suggestions, dropped=dict(validation.dropped))
        return view, validation.accepted

    def _vision_review(
        self,
        connection: Connection,
        workspace_id: UUID,
        document: Mapping[Any, Any],
        request: Mapping[str, Any],
        response: dict[str, Any],
        view: dict[str, Any],
    ) -> tuple[dict[str, Any], tuple[GroundedSuggestion, ...]]:
        """Vision readings mapped onto the document's correctable fields and lines."""

        versions = metadata.tables["document_version"]
        line_table = metadata.tables["doc_line"]
        doc_type = cast(Any, document["doc_type"])
        current_version_id = cast(UUID, document["current_version_id"])
        read_version_id = UUID(str(request["subject_id"]))
        pages = vision.load_pages(connection, workspace_id, read_version_id)
        try:
            context = vision.context_for(
                connection, workspace_id, current_version_id, doc_type, pages
            )
            validation = vision.validate_vision(VisionOutput.model_validate(response), context)
        except (InvalidOutput, ValidationError) as exc:
            code = exc.code if isinstance(exc, InvalidOutput) else "invalid_output"
            view.update(state="unavailable", code=code)
            return view, ()
        version_row = (
            connection.execute(
                sa.select(versions).where(
                    versions.c.workspace_id == workspace_id, versions.c.id == current_version_id
                )
            )
            .mappings()
            .one()
        )
        current_lines = [
            dict(row)
            for row in connection.execute(
                sa.select(line_table).where(
                    line_table.c.workspace_id == workspace_id,
                    line_table.c.document_version_id == current_version_id,
                )
            ).mappings()
        ]
        geometry = {page.page: page.render for page in pages}

        def boxes(page: int, box: Any) -> tuple[tuple[int, int, int, int], tuple[int, ...]]:
            render = geometry.get(page, {})
            width = int(render.get("work_width") or 1000)
            height = int(render.get("work_height") or 1000)
            pixels = (
                box.xmin * width // 1000,
                box.ymin * height // 1000,
                box.xmax * width // 1000,
                box.ymax * height // 1000,
            )
            return pixels, (box.ymin, box.xmin, box.ymax, box.xmax)

        accepted: list[GroundedSuggestion] = []
        header_columns = _VISION_HEADER_COLUMNS
        for item in validation.check.fields:
            target = header_columns.get(item.name)
            if target is None:
                continue
            field, kind = target
            pixels, box_2d = boxes(item.page, item.box)
            value = item.value.isoformat() if isinstance(item.value, date) else str(item.value)
            accepted.append(
                GroundedSuggestion(
                    scope="header",
                    line_no=None,
                    field=field,
                    kind=kind,
                    value=value,
                    typed=item.value,
                    page=item.page,
                    quote=item.text,
                    original_start=-1,
                    original_end=-1,
                    bbox=pixels,
                    box_2d=cast(tuple[int, int, int, int], box_2d),
                )
            )
        positions = align_vision_lines(current_lines, validation.check.lines)
        next_no = max((int(row["line_no"]) for row in current_lines), default=0)
        for line, matched in zip(validation.check.lines, positions, strict=True):
            if matched is None:
                next_no += 1
                matched = next_no
            pixels, box_2d = boxes(line.page, line.box)
            for cell in line.cells:
                target = _VISION_LINE_FIELDS.get(cell.name)
                if target is None:
                    continue
                field, kind = target
                accepted.append(
                    GroundedSuggestion(
                        scope="line",
                        line_no=matched,
                        field=field,
                        kind=kind,
                        value=str(cell.value),
                        typed=cell.value,
                        page=line.page,
                        quote=cell.text,
                        original_start=-1,
                        original_end=-1,
                        bbox=pixels,
                        box_2d=cast(tuple[int, int, int, int], box_2d),
                    )
                )
        lines_by_no = {int(row["line_no"]): row for row in current_lines}
        suggestions = []
        for item in accepted:
            if item.scope == "header":
                column = _VISION_COLUMNS.get(item.field, item.field)
                current = version_row.get(column)
            else:
                row = lines_by_no.get(cast(int, item.line_no))
                line_column = _correction_line_columns(str(document["doc_type"])).get(item.field)
                current = (
                    row.get(line_column) if row is not None and line_column is not None else None
                )
            suggestions.append(
                {
                    "scope": item.scope,
                    "line_no": item.line_no,
                    "field": item.field,
                    "value": item.value,
                    "current_value": current,
                    "matches_current": _suggestion_matches(item, current),
                    "page": item.page,
                    "quote": item.quote,
                    "start": None,
                    "end": None,
                    "box": list(item.box_2d) if item.box_2d else None,
                }
            )
        view.update(
            state="available",
            suggestions=suggestions,
            dropped=dict(validation.check.dropped),
            annotations=[
                {
                    "kind": note.kind,
                    "text": note.text,
                    "looks_like": note.looks_like,
                    "page": note.page,
                    "box": note.box.as_list(),
                }
                for note in validation.check.annotations
            ],
        )
        return view, tuple(accepted)

    @requires("data.view")
    def ai_suggestions(self, workspace_id: UUID, document_id: UUID) -> dict[str, Any] | None:
        """Advisory H1 suggestions for a document; nothing here changes the document."""

        document_table = metadata.tables["document"]
        with self.engine.connect() as connection:
            document = (
                connection.execute(
                    sa.select(document_table).where(
                        document_table.c.workspace_id == workspace_id,
                        document_table.c.id == document_id,
                        document_table.c.withdrawn_at.is_(None),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if document is None:
                return None
            view, _accepted = self._h1_review(connection, workspace_id, document)
        return view

    def _ai_reference_evidence(
        self,
        connection: Connection,
        workspace_id: UUID,
        document: Mapping[Any, Any],
        refs: Sequence[AiSuggestionRef],
        *,
        new_version: Mapping[str, Any],
        new_lines: Sequence[Mapping[str, Any]],
    ) -> dict[tuple[str, int | None, str], tuple[UUID, GroundedSuggestion]]:
        """Check each reference against a grounded suggestion with the saved value."""

        if not refs:
            return {}
        view, accepted = self._h1_review(connection, workspace_id, document)
        if view["state"] != "available" or view["ai_call_id"] is None:
            raise IngestionFailure(
                "AI_SUGGESTION_UNAVAILABLE",
                "The AI suggestions for this document are no longer available.",
            )
        ai_call_id = cast(UUID, view["ai_call_id"])
        lines_by_no = {int(row["line_no"]): row for row in new_lines}
        header_columns = _correction_header_columns(str(document["doc_type"]))
        line_columns = _correction_line_columns(str(document["doc_type"]))
        evidence: dict[tuple[str, int | None, str], tuple[UUID, GroundedSuggestion]] = {}
        for ref in refs:
            key = (ref.scope, ref.line_no, ref.field)
            if ref.ai_call_id != ai_call_id or key in evidence:
                raise IngestionFailure(
                    "AI_SUGGESTION_REF_INVALID",
                    "An AI suggestion reference is stale or repeated.",
                )
            if ref.scope == "header":
                column = header_columns.get(ref.field)
                saved = new_version.get(column) if column else None
            else:
                column = line_columns.get(ref.field)
                line = lines_by_no.get(ref.line_no or 0)
                saved = line.get(column) if column and line is not None else None
            match = next(
                (
                    item
                    for item in accepted
                    if (item.scope, item.line_no, item.field) == key
                    and _suggestion_matches(item, saved)
                ),
                None,
            )
            if match is None:
                raise IngestionFailure(
                    "AI_SUGGESTION_REF_INVALID",
                    "A value marked as AI-suggested does not match a grounded suggestion.",
                )
            evidence[key] = (ai_call_id, match)
        return evidence

    @requires("data.view")
    def document_source(self, workspace_id: UUID, document_id: UUID) -> dict[str, Any] | None:
        document = metadata.tables["document"]
        version = metadata.tables["document_version"]
        source = metadata.tables["source_file"]
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    sa.select(
                        source.c.id, source.c.object_key, source.c.display_name, source.c.format
                    )
                    .select_from(
                        document.join(
                            version,
                            sa.and_(
                                document.c.workspace_id == version.c.workspace_id,
                                document.c.current_version_id == version.c.id,
                            ),
                        ).join(
                            source,
                            sa.and_(
                                source.c.workspace_id == version.c.workspace_id,
                                source.c.id == version.c.source_file_id,
                            ),
                        )
                    )
                    .where(
                        document.c.workspace_id == workspace_id,
                        document.c.id == document_id,
                        document.c.withdrawn_at.is_(None),
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None

    @requires("data.view")
    def admin_errors(self, workspace_id: UUID) -> dict[str, Any]:
        source = metadata.tables["source_file"]
        warning = metadata.tables["document_warning"]
        version = metadata.tables["document_version"]
        with self.engine.connect() as connection:
            failure_day = sa.cast(source.c.parse_finished_at, sa.Date)
            failures = (
                connection.execute(
                    sa.select(
                        source.c.error_code,
                        failure_day.label("day"),
                        sa.func.count(source.c.id).label("count"),
                    )
                    .where(
                        source.c.workspace_id == workspace_id,
                        source.c.status == "failed",
                        source.c.removed_at.is_(None),
                    )
                    .group_by(source.c.error_code, failure_day)
                    .order_by(failure_day.desc())
                )
                .mappings()
                .all()
            )
            layouts = (
                connection.execute(
                    sa.select(source.c.layout_signature, sa.func.count(source.c.id).label("count"))
                    .where(
                        source.c.workspace_id == workspace_id,
                        source.c.status == "failed",
                        source.c.layout_signature.is_not(None),
                    )
                    .group_by(source.c.layout_signature)
                    .order_by(sa.func.count(source.c.id).desc())
                    .limit(10)
                )
                .mappings()
                .all()
            )
            vendors = (
                connection.execute(
                    sa.select(
                        version.c.vendor_name,
                        sa.func.count(sa.distinct(source.c.id)).label("count"),
                    )
                    .select_from(
                        source.join(
                            version,
                            sa.and_(
                                source.c.workspace_id == version.c.workspace_id,
                                source.c.id == version.c.source_file_id,
                            ),
                        )
                    )
                    .where(source.c.workspace_id == workspace_id, source.c.status == "failed")
                    .group_by(version.c.vendor_name)
                    .order_by(sa.func.count(sa.distinct(source.c.id)).desc())
                    .limit(10)
                )
                .mappings()
                .all()
            )
            warnings = (
                connection.execute(
                    sa.select(
                        warning.c.id,
                        warning.c.source_file_id,
                        warning.c.document_id,
                        warning.c.code,
                        warning.c.level,
                        warning.c.message,
                        warning.c.detail,
                        warning.c.created_at,
                    )
                    .where(warning.c.workspace_id == workspace_id, warning.c.resolved_at.is_(None))
                    .order_by(warning.c.created_at.desc())
                    .limit(500)
                )
                .mappings()
                .all()
            )
            retryable = (
                connection.execute(
                    sa.select(
                        source.c.id,
                        source.c.display_name,
                        source.c.error_code,
                        source.c.parse_finished_at,
                    )
                    .where(
                        source.c.workspace_id == workspace_id,
                        source.c.status == "failed",
                        source.c.removed_at.is_(None),
                    )
                    .order_by(source.c.parse_finished_at.desc())
                    .limit(100)
                )
                .mappings()
                .all()
            )
        return {
            "failed_by_reason_and_day": [dict(row) for row in failures],
            "top_failing_layouts": [dict(row) for row in layouts],
            "top_failing_vendors": [dict(row) for row in vendors],
            "unresolved_warnings": [dict(row) for row in warnings],
            "retry_actions": [dict(row) for row in retryable],
        }


def _correction_qa(
    version: Mapping[str, Any], lines: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """TR-PAR-5 arithmetic on a corrected version (the parser's ±1 tolerances)."""

    def number(value: Any) -> Decimal | None:
        return Decimal(str(value)) if value is not None else None

    checked, failures = arithmetic_failures(
        [
            (
                int(row["line_no"]),
                number(row.get("quantity")),
                number(row.get("rate")),
                number(row.get("discount")),
                number(row.get("amount")),
            )
            for row in sorted(lines, key=lambda item: int(item["line_no"]))
        ],
        taxable_total=number(version.get("taxable_total")),
        taxes=[
            number(version.get("cgst_total")),
            number(version.get("sgst_total")),
            number(version.get("igst_total")),
        ],
        round_off=number(version.get("round_off")),
        grand_total=number(version.get("grand_total")),
    )
    return {"checked": checked, "passed": not failures, "failures": list(failures)}


# Vision field → (correction field, value kind); the correction field names the
# document_version column through INVOICE_HEADER_COLUMNS or _VISION_COLUMNS.
_VISION_HEADER_COLUMNS: Mapping[str, tuple[str, FieldKind]] = {
    "doc_no": ("doc_no", "ref"),
    "doc_date": ("doc_date", "date"),
    "supplier_name": ("vendor_name", "text"),
    "supplier_gstin": ("vendor_gstin", "gstin"),
    "buyer_gstin": ("buyer_gstin", "gstin"),
    "currency": ("currency", "currency"),
    "taxable_total": ("taxable_total", "money"),
    "cgst_total": ("cgst", "money"),
    "sgst_total": ("sgst", "money"),
    "igst_total": ("igst", "money"),
    "round_off": ("round_off", "money"),
    "grand_total": ("grand_total", "money"),
    "po_ref": ("po_refs", "ref"),
    "grn_ref": ("grn_refs", "ref"),
    "challan_no": ("challan_no", "ref"),
    "e_way_bill_no": ("e_way_bill_no", "ref"),
    "irn": ("irn", "ref"),
    "place_of_supply": ("place_of_supply", "text"),
    "supplier_invoice_no": ("supplier_invoice_no", "ref"),
    "ship_to_name": ("ship_to", "text"),
    "payment_terms": ("payment_terms", "text"),
    "delivery_date": ("delivery_date", "date"),
}
_VISION_COLUMNS: Mapping[str, str] = {
    **INVOICE_HEADER_COLUMNS,
    "supplier_invoice_no": "supplier_invoice_no",
    "ship_to": "ship_to",
    "payment_terms": "payment_terms_text",
    "delivery_date": "delivery_date",
}
_VISION_LINE_FIELDS: Mapping[str, tuple[str, FieldKind]] = {
    "description": ("description", "text"),
    "item_code": ("item_code", "ref"),
    "hsn_sac": ("hsn_sac", "ref"),
    "uom": ("uom", "ref"),
    "quantity": ("quantity", "number"),
    "rate": ("rate", "number"),
    "amount": ("amount", "money"),
}


def _suggestion_matches(item: GroundedSuggestion, value: Any) -> bool:
    """Whether a stored or saved value equals a grounded suggestion's typed value."""

    if value is None:
        return False
    if item.field in {"po_refs", "grn_refs"}:
        values = value if isinstance(value, (list, tuple)) else [value]
        return any(str(entry).strip() == item.typed for entry in values)
    kind = item.kind
    if kind in {"money", "number"}:
        try:
            return Decimal(str(value)) == item.typed
        except ArithmeticError:
            return False
    if kind == "date":
        return value == item.typed if isinstance(value, date) else str(value) == str(item.typed)
    try:
        return typed_value(kind, str(value).strip()) == item.typed
    except ValueError:
        return False


def _page_split_mode(file_row: Mapping[str, Any]) -> PageSplit:
    """A person's answer to a proposed page split; PDFs never carry a type mismatch."""

    if file_row.get("format") != FileFormat.PDF.value:
        return PageSplit.AUTO
    return {"accepted": PageSplit.CONFIRMED, "kept": PageSplit.OFF}.get(
        str(file_row.get("mismatch_state")), PageSplit.AUTO
    )


def sniff_document_type(content: bytes, file_format: FileFormat) -> DocType | None:
    """Run the pure parser without a zone hint and return one unambiguous type."""

    if file_format not in {FileFormat.XML, FileFormat.CSV}:
        return None
    result = parse(content, ParserHints(file_format=file_format))
    doc_types = {document.header.doc_type for document in result.documents}
    if len(doc_types) == 1:
        return next(iter(doc_types))
    if file_format is FileFormat.CSV:
        text = content[: 64 * 1024].decode("utf-8-sig", errors="replace")
        try:
            rows = list(csv.reader(io.StringIO(text)))[:8]
        except csv.Error:
            return None
        header_text = " ".join(",".join(row) for row in rows).upper()
        markers = {
            DocType.PO: ("PURCHASE ORDER", "PO NO"),
            DocType.GRN: ("GOODS RECEIPT", "RECEIPT NO", "GRN", "MRN"),
            DocType.INVOICE: ("INVOICE", "BILL NO"),
        }
        candidates = {
            doc_type
            for doc_type, cues in markers.items()
            if any(cue in header_text for cue in cues)
        }
        if len(candidates) == 1:
            return next(iter(candidates))
    return None


def _media_type(file_format: FileFormat) -> str:
    return {
        FileFormat.PDF: "application/pdf",
        FileFormat.XML: "application/xml",
        FileFormat.CSV: "text/csv; charset=utf-8",
        FileFormat.ZIP: "application/zip",
    }[file_format]


def _extraction_method(file_format: FileFormat | None, *, is_ocr: bool = False) -> ExtractionMethod:
    if file_format is FileFormat.PDF and is_ocr:
        return ExtractionMethod.PDF_OCR
    return {
        FileFormat.XML: ExtractionMethod.XML_PROFILE,
        FileFormat.CSV: ExtractionMethod.CSV_MAPPING,
        FileFormat.PDF: ExtractionMethod.PDF_TEXT,
    }.get(file_format, ExtractionMethod.SHELL)


def _field_origin(
    value: FieldValue[Any], extraction_method: ExtractionMethod
) -> FieldOrigin | None:
    # A value read by AI is origin AI whatever else it carries, so the reference
    # system check never accepts it (PD-2a).
    if value.source is FieldSource.AI:
        return FieldOrigin.AI
    if value.origin is not None:
        return value.origin
    if value.source is FieldSource.MANUAL or extraction_method is ExtractionMethod.MANUAL:
        return FieldOrigin.MANUAL
    if extraction_method in {ExtractionMethod.AI_FALLBACK, ExtractionMethod.AI_FIRST}:
        return FieldOrigin.AI
    if extraction_method in {ExtractionMethod.XML_PROFILE, ExtractionMethod.CSV_MAPPING}:
        return FieldOrigin.ERP_IMPORT
    if extraction_method in {ExtractionMethod.PDF_TEXT, ExtractionMethod.PDF_OCR}:
        return FieldOrigin.OCR
    return None


def _validated_archive_path(value: str) -> str | None:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or path.is_absolute()
        or re.match(r"^[A-Za-z]:", normalized)
        or ".." in path.parts
    ):
        return None
    return "/".join(part for part in path.parts if part not in {"", "."}) or None


def _is_skipped_archive_name(value: str) -> bool:
    parts = value.replace("\\", "/").split("/")
    return any(part.casefold() == "__macosx" or part.startswith(".") for part in parts)


def _aggregate_status(statuses: list[str]) -> str:
    if not statuses:
        return "failed"
    terminal = {"parsed", "partial", "failed", "removed"}
    if any(status not in terminal for status in statuses):
        return "queued"
    successful = [status for status in statuses if status in {"parsed", "partial"}]
    if not successful:
        return "failed"
    if len(successful) != len(statuses) or "partial" in successful:
        return "partial"
    return "parsed"


def _header_values(header: Any) -> dict[str, Any]:
    return {name: getattr(header, name) for name in type(header).model_fields if name != "extras"}


def _persisted_verified_field_confidence(
    connection: Connection,
    *,
    workspace_id: UUID,
    version_id: UUID,
    extraction_method: str,
) -> Decimal | None:
    evidence = metadata.tables["field_evidence"]
    structured = extraction_method in {"xml_profile", "csv_mapping", "manual"}
    rows = connection.execute(
        sa.select(
            evidence.c.confidence,
            evidence.c.source,
            evidence.c.verification,
        ).where(
            evidence.c.workspace_id == workspace_id,
            evidence.c.document_version_id == version_id,
        )
    ).all()
    values = []
    for row in rows:
        if row.verification == FieldVerification.USER.value:
            values.append(Decimal("1"))
        elif (
            row.verification == FieldVerification.SYSTEM_CHECK.value and row.confidence is not None
        ) or (
            row.source == FieldSource.RULE.value
            and row.confidence is not None
            and (structured or Decimal(str(row.confidence)) >= Decimal("0.98"))
        ):
            values.append(Decimal(str(row.confidence)))
    return min(values) if values else None


def _parser_synonym_overrides(raw: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Translate the product's synonym groups into parser field aliases.

    The seed config follows PRD Appendix B groups (for example ``invoice_no`` and
    ``totals``), while the parser needs schema field names (``doc_no`` and
    ``grand_total``). Passing the groups through directly makes category labels
    shadow real fields such as Invoice No and Invoice Date.
    """

    supported_fields = {
        "doc_no",
        "doc_date",
        "vendor_name",
        "vendor_gstin",
        "buyer_gstin",
        "currency",
        "taxable_total",
        "cgst",
        "sgst",
        "igst",
        "round_off",
        "grand_total",
        "revision",
        "status",
        "ship_to",
        "payment_terms",
        "delivery_date",
        "po_refs",
        "grn_refs",
        "challan_no",
        "supplier_invoice_no",
        "e_way_bill_no",
        "irn",
        "place_of_supply",
        "line_no",
        "line_type",
        "description",
        "item_code",
        "hsn_sac",
        "uom",
        "quantity",
        "received_qty",
        "rejected_qty",
        "accepted_qty",
        "rate",
        "discount",
        "amount",
        "tax_rate",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "po_ref",
        "po_line_ref",
    }
    grouped_fields = {
        "invoice_no": "doc_no",
        "dates": "doc_date",
        "totals": "grand_total",
    }
    rate_labels = {"RATE", "UNIT PRICE", "PRICE UNIT", "BASIC RATE", "UNIT RATE"}
    tax_fields = {
        "CGST": {"CGST", "CENTRAL TAX"},
        "SGST": {"SGST", "UTGST", "STATE TAX"},
        "IGST": {"IGST", "INTEGRATED TAX"},
        "hsn_sac": {"HSN", "HSN CODE", "HSN SAC", "SAC"},
        "irn": {"IRN"},
    }
    converted: dict[str, list[str]] = {}

    def add(field_name: str, alias: str) -> None:
        converted.setdefault(field_name, []).append(alias)

    for key, raw_aliases in raw.items():
        if not isinstance(raw_aliases, (list, tuple)):
            continue
        if key in supported_fields:
            for alias in raw_aliases:
                if isinstance(alias, str):
                    add(key, alias)
            continue
        target = grouped_fields.get(key)
        if target is not None:
            for alias in raw_aliases:
                if isinstance(alias, str):
                    add(target, alias)
            continue
        if key == "rate_amount":
            for alias in raw_aliases:
                if not isinstance(alias, str):
                    continue
                normalized = re.sub(r"[^A-Z0-9]+", " ", alias.upper()).strip()
                add("rate" if normalized in rate_labels else "amount", alias)
            continue
        if key == "tax":
            for alias in raw_aliases:
                if not isinstance(alias, str):
                    continue
                normalized = re.sub(r"[^A-Z0-9]+", " ", alias.upper()).strip()
                for field_name, labels in tax_fields.items():
                    if normalized in labels:
                        add(field_name, alias)
                        break

    return {key: tuple(dict.fromkeys(aliases)) for key, aliases in converted.items()}


def _plain(value: Any) -> Any:
    if isinstance(value, FieldValue):
        value = value.value
    if hasattr(value, "value"):
        return value.value
    return value


def _verified_by_review(
    row: Mapping[str, Any], *, references: Collection[str], shown_key_fields: Collection[str]
) -> bool:
    """Whether marking a document reviewed verifies this unchanged value (P2, PD-2b).

    References are verified as before, and an invoice's key values where the person was
    shown them. A value read by AI is never verified this way: it waits for its own Verify.
    """

    if row["origin"] == FieldOrigin.AI.value or row["source"] == FieldSource.AI.value:
        return False
    field_name = str(row["field_name"])
    return field_name in references or field_name in shown_key_fields


# Rules readings of a PDF: the OCR cross-check a value read by AI is shown beside (PD-4d).
_CROSS_CHECK_METHODS = frozenset({ExtractionMethod.PDF_TEXT.value, ExtractionMethod.PDF_OCR.value})


def _read_by_ai(row: Mapping[str, Any]) -> bool:
    return row["origin"] == FieldOrigin.AI.value or row["source"] == FieldSource.AI.value


def _latest_evidence(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[UUID | None, UUID | None, str], Mapping[str, Any]]:
    """Each value's latest evidence row, by (version, line, field)."""

    latest: dict[tuple[UUID | None, UUID | None, str], Mapping[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (item["created_at"], str(item["id"]))):
        latest[(row["document_version_id"], row["doc_line_id"], str(row["field_name"]))] = row
    return latest


def _values_agree(first: str | None, second: str | None) -> bool:
    """Whether a value read by AI and its OCR cross-check say the same (Q8)."""

    if first is None or second is None:
        return False
    try:
        return Decimal(first) == Decimal(second)
    except (InvalidOperation, ValueError):
        return " ".join(first.split()).casefold() == " ".join(second.split()).casefold()


def _cross_check_values(
    connection: Connection,
    workspace_id: UUID,
    *,
    document_id: UUID,
    version: Mapping[str, Any],
) -> dict[tuple[int | None, str], str | None]:
    """The values the rules read from the same file, by (line number, field).

    The cross-check is the latest earlier version of the document that the rules read
    from the same source file; values read by AI never cross-check each other.
    """

    version_table = metadata.tables["document_version"]
    line_table = metadata.tables["doc_line"]
    evidence_table = metadata.tables["field_evidence"]
    if version["source_file_id"] is None:
        return {}
    previous = connection.scalar(
        sa.select(version_table.c.id)
        .where(
            version_table.c.workspace_id == workspace_id,
            version_table.c.document_id == document_id,
            version_table.c.source_file_id == version["source_file_id"],
            version_table.c.seq < version["seq"],
            version_table.c.extraction_method.in_(sorted(_CROSS_CHECK_METHODS)),
        )
        .order_by(version_table.c.seq.desc())
        .limit(1)
    )
    if previous is None:
        return {}
    line_no = {
        cast(UUID, row.id): int(row.line_no)
        for row in connection.execute(
            sa.select(line_table.c.id, line_table.c.line_no).where(
                line_table.c.workspace_id == workspace_id,
                line_table.c.document_version_id == previous,
            )
        )
    }
    rows = (
        connection.execute(
            sa.select(*evidence_table.c).where(
                evidence_table.c.workspace_id == workspace_id,
                sa.or_(
                    evidence_table.c.document_version_id == previous,
                    evidence_table.c.doc_line_id.in_(list(line_no)) if line_no else sa.false(),
                ),
            )
        )
        .mappings()
        .all()
    )
    values: dict[tuple[int | None, str], str | None] = {}
    for row in _latest_evidence(rows).values():
        if _read_by_ai(row):
            continue
        owner = row["doc_line_id"]
        if owner is not None and owner not in line_no:
            continue
        number = None if owner is None else line_no[owner]
        values[(number, str(row["field_name"]))] = row["value_text"]
    return values


def _ai_values(
    connection: Connection,
    workspace_id: UUID,
    *,
    document_id: UUID,
    version: Mapping[str, Any],
    lines: Sequence[Mapping[str, Any]],
    evidences: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The current version's values read by AI, each beside its OCR cross-check (PD-4d)."""

    line_no = {cast(UUID, row["id"]): int(row["line_no"]) for row in lines}
    read = [row for row in _latest_evidence(evidences).values() if _read_by_ai(row)]
    if not read:
        return []
    cross_check = _cross_check_values(
        connection, workspace_id, document_id=document_id, version=version
    )
    values = []
    for row in read:
        owner = row["doc_line_id"]
        if owner is not None and owner not in line_no:
            continue
        number = None if owner is None else line_no[owner]
        name = str(row["field_name"])
        ocr_value = cross_check.get((number, name))
        values.append(
            {
                "field_name": name,
                "line_id": owner,
                "line_no": number,
                "label": name if number is None else f"line {number} {name}",
                "value_text": row["value_text"],
                "ocr_value_text": ocr_value,
                "agrees": _values_agree(row["value_text"], ocr_value),
                "verified": row["verification"] == FieldVerification.USER.value,
                "page": row["page"],
                "bbox": row["bbox"],
            }
        )
    return sorted(
        values,
        key=lambda item: (item["line_no"] is not None, item["line_no"] or 0, item["field_name"]),
    )


def _field_text(value: Any) -> str:
    value = _plain(value)
    if isinstance(value, (tuple, list)):
        return ";".join(_field_text(item) for item in value)
    if isinstance(value, (date, Decimal)):
        return str(value)
    return str(value)


def _persisted_reference_values(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(part for part in value.split(";") if part)


def _same_reference_values(left: Sequence[str], right: Sequence[str]) -> bool:
    return sorted(canonicalize_document_number(value) for value in left) == sorted(
        canonicalize_document_number(value) for value in right
    )


def _preserve_user_verified_references(
    connection: Connection,
    *,
    workspace_id: UUID,
    version_id: UUID,
    document: Document,
) -> tuple[Document, bool]:
    """Carry a human-verified PO/GRN reference forward only when it still agrees."""

    evidence = metadata.tables["field_evidence"]
    line_table = metadata.tables["doc_line"]
    header_rows = connection.execute(
        sa.select(
            evidence.c.field_name,
            evidence.c.value_text,
            evidence.c.source,
            evidence.c.origin,
            evidence.c.confidence,
            evidence.c.page,
            evidence.c.bbox,
            evidence.c.snippet,
            evidence.c.verified_by,
            evidence.c.verified_at,
        )
        .where(
            evidence.c.workspace_id == workspace_id,
            evidence.c.document_version_id == version_id,
            evidence.c.field_name.in_(("po_refs", "grn_refs")),
            evidence.c.verification == FieldVerification.USER.value,
        )
        .order_by(evidence.c.created_at, evidence.c.id)
    ).mappings()
    line_rows = connection.execute(
        sa.select(
            line_table.c.line_no,
            evidence.c.value_text,
            evidence.c.source,
            evidence.c.origin,
            evidence.c.confidence,
            evidence.c.page,
            evidence.c.bbox,
            evidence.c.snippet,
            evidence.c.verified_by,
            evidence.c.verified_at,
        )
        .select_from(line_table.join(evidence, evidence.c.doc_line_id == line_table.c.id))
        .where(
            line_table.c.workspace_id == workspace_id,
            line_table.c.document_version_id == version_id,
            evidence.c.workspace_id == workspace_id,
            evidence.c.field_name == "po_ref",
            evidence.c.verification == FieldVerification.USER.value,
        )
        .order_by(line_table.c.line_no, evidence.c.created_at, evidence.c.id)
    ).mappings()

    header_updates: dict[str, Any] = {}

    def preserve_field_value(
        field: FieldValue[Any], row: RowMapping, value: Any
    ) -> FieldValue[Any]:
        source = FieldSource(str(row["source"]))
        persisted_origin = row["origin"]
        origin = (
            FieldOrigin(str(persisted_origin))
            if persisted_origin is not None
            else (FieldOrigin.MANUAL if source is FieldSource.MANUAL else field.origin)
        )
        bbox_value = row["bbox"]
        bbox = (
            cast(
                tuple[Decimal, Decimal, Decimal, Decimal],
                tuple(Decimal(str(item)) for item in bbox_value),
            )
            if isinstance(bbox_value, (list, tuple)) and len(bbox_value) == 4
            else None
        )
        return field.model_copy(
            update={
                "value": value,
                "source": source,
                "confidence": cast(Decimal | None, row["confidence"]),
                "origin": origin,
                "verification": FieldVerification.USER,
                "verified_by": str(row["verified_by"]) if row["verified_by"] else None,
                "verified_at": row["verified_at"],
                "evidence": FieldEvidence(
                    page=int(row["page"]) if row["page"] is not None else None,
                    bbox=bbox,
                    snippet=str(row["snippet"]) if row["snippet"] else None,
                ),
            }
        )

    for row in header_rows:
        field_name = str(row["field_name"])
        incoming = getattr(document.header, field_name, None)
        old_values = _persisted_reference_values(cast(str | None, row["value_text"]))
        incoming_values = (
            tuple(str(item) for item in incoming.value) if incoming is not None else ()
        )
        if not _same_reference_values(old_values, incoming_values):
            return document, True
        if incoming is None:
            return document, True
        header_updates[field_name] = preserve_field_value(incoming, row, old_values)

    line_updates: dict[int, DocLine] = {}
    for row in line_rows:
        line_no = int(row["line_no"])
        line = next((item for item in document.lines if item.line_no == line_no), None)
        old_values = _persisted_reference_values(cast(str | None, row["value_text"]))
        incoming_values = (str(line.po_ref.value),) if line and line.po_ref is not None else ()
        if not _same_reference_values(old_values, incoming_values):
            return document, True
        assert line is not None and line.po_ref is not None
        line_updates[line_no] = line.model_copy(
            update={"po_ref": preserve_field_value(line.po_ref, row, old_values[0])}
        )
    if not header_updates and not line_updates:
        return document, False
    header = document.header.model_copy(update=header_updates)
    lines = tuple(line_updates.get(line.line_no, line) for line in document.lines)
    return document.model_copy(update={"header": header, "lines": lines}), False


def _reference_values(value: Any) -> list[str]:
    plain = _plain(value)
    if plain is None:
        return []
    if isinstance(plain, (tuple, list)):
        return [str(item) for item in plain if str(item).strip()]
    return [str(plain)]


def _vendor_identity(gstin: str | None, name: str | None) -> str | None:
    if gstin:
        normalized = re.sub(r"[^A-Z0-9]", "", gstin.upper())
        return normalized or None
    if name:
        return normalize_vendor_name(name) or None
    return None


def _canonical_optional(value: Any) -> str | None:
    plain = _plain(value)
    return canonicalize_document_number(str(plain)) if plain else None


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


def _revision_rank(value: str | None) -> int:
    if not value:
        return 0
    digits = "".join(re.findall(r"\d+", value))
    return int(digits) if digits else 0


def _payment_days(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\b(?:net|credit|within)?\s*(\d{1,3})\s*(?:days?|d)?\b", value, re.I)
    return int(match.group(1)) if match else None


def _canonical_hash(value: Any) -> str:
    from reconbench_app.json_serialization import to_jsonable

    encoded = json.dumps(
        to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    from reconbench_app.json_serialization import to_jsonable

    return to_jsonable(value)


__all__ = [
    "BatchVersionConflict",
    "IngestionFailure",
    "IngestionLimits",
    "IngestionService",
    "ObjectStoreUnavailable",
    "PARSER_VERSION",
    "UploadReceipt",
    "safe_display_name",
    "sniff_document_type",
    "sniff_format",
]
