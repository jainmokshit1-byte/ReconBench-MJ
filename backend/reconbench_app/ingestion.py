"""Workspace-scoped upload, archive, parse, and document persistence services."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Protocol, cast
from uuid import UUID

import sqlalchemy as sa
from botocore.exceptions import BotoCoreError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from reconbench_core.normalize import (
    canonicalize_document_number,
    normalize_item_text,
    normalize_uom,
    normalize_vendor_name,
)
from reconbench_core.parsers import ParseResult, ParserHints, parse
from reconbench_core.parsers.types import ParseWarning
from reconbench_core.schema import (
    DocType,
    Document,
    ExtractionMethod,
    FieldValue,
    FileFormat,
    UploadZone,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from reconbench_app.audit import write_audit_event
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.pdf_sandbox import parse_pdf_isolated
from reconbench_app.storage import ObjectStore

PARSER_VERSION = "phase4-parser-2"
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
    ) -> None:
        self.engine = engine
        self.object_store = object_store
        self.queue = queue
        self.ocr_languages = ocr_languages

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
                connection.execute(
                    sa.update(table)
                    .where(table.c.id == existing["id"])
                    .values(reupload_skips=table.c.reupload_skips + 1, version=table.c.version + 1)
                )
                return UploadReceipt(
                    file_id=existing["id"],
                    sha256=sha256,
                    size_bytes=int(existing["size_bytes"]),
                    format=str(existing["format"]),
                    status=str(existing["status"]),
                    duplicate=True,
                )

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
            if row["status"] not in {"failed", "partial"}:
                raise IngestionFailure(
                    "FILE_NOT_RETRYABLE", "Only failed or partial files can be retried."
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
                detail={"manual_retry": True},
            )
        return {"file_id": file_id, "job_id": job_id, "status": "queued"}

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
            dirty_scopes: set[tuple[str, UUID]] = set()
            for row in affected:
                doc_id = cast(UUID, row["id"])
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
                        .where(
                            document_version.c.workspace_id == actor.workspace_id,
                            document_version.c.document_id == doc_id,
                            source.c.removed_at.is_(None),
                        )
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
                        .where(
                            document.c.id == doc_id, document.c.workspace_id == actor.workspace_id
                        )
                        .values(
                            current_version_id=None,
                            withdrawn_at=sa.func.now(),
                            withdrawn_by=actor.actor_id,
                            updated_at=sa.func.now(),
                            input_seq=document.c.input_seq + (1 if row["doc_type"] == "po" else 0),
                            version=document.c.version + 1,
                        )
                    )
                else:
                    connection.execute(
                        sa.update(document)
                        .where(
                            document.c.id == doc_id, document.c.workspace_id == actor.workspace_id
                        )
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
                    workspace_id=actor.workspace_id,
                    file_id=file_id,
                    document_id=doc_id,
                    document_version_id=cast(UUID | None, live_version["id"])
                    if live_version is not None
                    else None,
                    stage="gated",
                    outcome="warn",
                    detail={
                        "source_removed": True,
                        "document_withdrawn": live_version is None,
                        "current_version_id": str(live_version["id"])
                        if live_version is not None
                        else None,
                    },
                )

            for scope_kind, scope_id in dirty_scopes:
                self._mark_dirty_scope(
                    connection,
                    workspace_id=actor.workspace_id,
                    scope_kind=scope_kind,
                    scope_id=scope_id,
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
            result = parse_pdf_isolated(content, parser_hints, languages=self.ocr_languages)
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

    def _parser_hints(self, workspace_id: UUID, file_row: Mapping[str, Any]) -> ParserHints:
        body = self._active_config_body(workspace_id)
        engine_body = body.get("engine", {})
        synonyms = engine_body.get("synonyms", {}) if isinstance(engine_body, Mapping) else {}
        uom = engine_body.get("uom", {}) if isinstance(engine_body, Mapping) else {}
        policies = (
            engine_body.get("charge_keywords", []) if isinstance(engine_body, Mapping) else []
        )
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
        return ParserHints(
            doc_type=zone_type,
            file_format=FileFormat(file_row["format"]),
            synonyms=_parser_synonym_overrides(synonyms),
            column_mapping=column_mapping,
            uom_aliases=dict(uom.get("aliases", {})) if isinstance(uom, Mapping) else {},
            charge_keywords=tuple(policies) if policies else ParserHints().charge_keywords,
            max_bytes=limits.max_file_bytes,
            max_pages=limits.max_pages,
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
            or warning.code in {"RECORD_PARSE_ERROR", "DOC_TYPE_REQUIRED"}
            for warning in result.warnings
        )
        status = "failed" if parse_failed else "partial" if partial else "parsed"
        error_code = result.reason_code if parse_failed else None
        with self.engine.begin() as connection:
            documents_new = documents_updated = documents_skipped = 0
            document_ids: list[UUID] = []
            if not parse_failed:
                hints = self._parser_hints(workspace_id, file_row)
                for document in result.documents:
                    outcome, document_id, version_id = self._upsert_document(
                        connection,
                        workspace_id=workspace_id,
                        batch_id=cast(UUID | None, file_row["batch_id"]),
                        file_id=file_id,
                        actor_id=cast(UUID | None, file_row["uploaded_by"]),
                        document=document,
                        file_sha=cast(str, file_row["sha256"]),
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
                    document_ids.append(document_id)
                    if outcome == "new":
                        documents_new += 1
                    elif outcome == "updated":
                        documents_updated += 1
                    else:
                        documents_skipped += 1
                    if version_id is not None and outcome != "skipped" and result.page_texts:
                        text_table = metadata.tables["document_text"]
                        for page_text in result.page_texts:
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
                        else None
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
        if file_row["parent_file_id"] is not None:
            self._refresh_parent(cast(UUID, file_row["parent_file_id"]))

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
        natural_payload: dict[str, Any] = {
            "type": header.doc_type.value,
            "vendor_identity": vendor_identity,
            "canonical_doc_no": canonical_doc_no,
            "doc_date": doc_date.isoformat() if isinstance(doc_date, date) else None,
        }
        if not canonical_doc_no:
            natural_payload["missing_number_fallback"] = {
                "source_sha256": file_sha,
                "locator": document.source_locator,
            }
        natural_key = document.natural_key or _canonical_hash(natural_payload)
        existing = (
            connection.execute(
                sa.select(document_table)
                .where(
                    document_table.c.workspace_id == workspace_id,
                    document_table.c.natural_key == natural_key,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        created = existing is None
        document_id = cast(UUID, existing["id"]) if existing is not None else new_id()
        current_version_id = cast(UUID | None, existing["current_version_id"]) if existing else None
        current_hash = None
        if current_version_id is not None:
            current_hash = connection.scalar(
                sa.select(version_table.c.content_hash).where(
                    version_table.c.workspace_id == workspace_id,
                    version_table.c.id == current_version_id,
                )
            )
        if current_hash == content_hash:
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
        self._insert_lines_and_evidence(
            connection,
            workspace_id=workspace_id,
            version_id=version_id,
            document=document,
            uom_aliases=uom_aliases,
            charge_keywords=charge_keywords,
        )
        connection.execute(
            sa.update(document_table)
            .where(
                document_table.c.workspace_id == workspace_id, document_table.c.id == document_id
            )
            .values(
                current_version_id=version_id,
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
        uom_aliases: Mapping[str, str],
        charge_keywords: tuple[str, ...],
    ) -> None:
        line_table = metadata.tables["doc_line"]
        evidence_table = metadata.tables["field_evidence"]
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
                        confidence=value.confidence,
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
                            confidence=value.confidence,
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
        return dict(row) if row is not None else None

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
        return [dict(row) for row in rows]

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
            "processing_log": [dict(row) for row in events],
        }

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


def _field_text(value: Any) -> str:
    value = _plain(value)
    if isinstance(value, tuple):
        return ";".join(_field_text(item) for item in value)
    if isinstance(value, (date, Decimal)):
        return str(value)
    return str(value)


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
