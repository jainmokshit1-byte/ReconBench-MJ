"""Disposable-database tests for PD-0b-4: no duplicate ingestion (D103 decision 9)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from reconbench_app.authorization import ActorContext
from reconbench_app.db.metadata import metadata
from reconbench_app.ingestion import IngestionService
from reconbench_app.storage import LocalObjectStore
from reconbench_core.normalize import validate_gstin
from reconbench_core.schema import FileFormat, UploadZone
from sqlalchemy.engine import Engine
from tests.integration.test_phase4_task_411_413 import (
    _GSTIN,
    _HSN,
    _VENDOR_NAME,
    _csv_bytes,
    _Queue,
    _seed_workspace,
    _upload_csv,
    scratch_engine,
)

__all__ = ["scratch_engine"]

_INVOICE_MAPPING = {"PO Ref": "po_refs", "Invoice No": "doc_no", "Invoice Date": "doc_date"}
_OTHER_GSTIN = "29AABCU9603R1ZJ"


def _invoice(
    number: str,
    day: date,
    *,
    gstin: str = _GSTIN,
    vendor: str = _VENDOR_NAME,
    quantity: str = "2",
    note: str = "",
) -> bytes:
    amount = str(int(quantity) * 100)
    return (
        _csv_bytes(
            [
                "Invoice No",
                "Invoice Date",
                "PO Ref",
                "Vendor Name",
                "Vendor GSTIN",
                "Currency",
                "Taxable Total",
                "Grand Total",
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
                    number,
                    day.strftime("%d/%m/%Y"),
                    "PO-DUP-1",
                    vendor,
                    gstin,
                    "INR",
                    amount,
                    amount,
                    "1",
                    "Precision Widget PW-100",
                    _HSN,
                    "NOS",
                    quantity,
                    "100",
                    amount,
                ]
            ],
        )
        + note.encode()
    )


def _po(number: str, day: date, *, quantity: str = "4", note: str = "") -> bytes:
    amount = str(int(quantity) * 100)
    return (
        _csv_bytes(
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
                    number,
                    day.strftime("%d/%m/%Y"),
                    _VENDOR_NAME,
                    _GSTIN,
                    "INR",
                    amount,
                    amount,
                    "open",
                    "1",
                    "Precision Widget PW-100",
                    _HSN,
                    "NOS",
                    quantity,
                    "100",
                    amount,
                ]
            ],
        )
        + note.encode()
    )


def _setup(
    engine: Engine, label: str, tmp_path: Any
) -> tuple[ActorContext, IngestionService, UUID]:
    _workspace_id, actor = _seed_workspace(engine, label)
    ingestion = IngestionService(engine, LocalObjectStore(tmp_path / label), _Queue())
    batch_id = UUID(str(ingestion.create_batch(actor, label=f"PD-0b-4 {label}")["id"]))
    return actor, ingestion, batch_id


def _count(engine: Engine, table: str, **where: Any) -> int:
    tab = metadata.tables[table]
    with engine.connect() as connection:
        return int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(tab)
                .where(*(tab.c[key] == value for key, value in where.items()))
            )
            or 0
        )


def _invoices(engine: Engine, workspace_id: UUID, number: str) -> list[Any]:
    document = metadata.tables["document"]
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(document.c.id, document.c.withdrawn_at).where(
                    document.c.workspace_id == workspace_id,
                    document.c.doc_type == "invoice",
                    document.c.doc_no == number,
                )
            )
        )


def _upload_invoice(
    ingestion: IngestionService,
    actor: ActorContext,
    batch_id: UUID,
    name: str,
    content: bytes,
    job: int,
) -> UUID:
    return _upload_csv(
        ingestion,
        actor,
        batch_id,
        zone=UploadZone.INVOICE,
        display_name=name,
        content=content,
        job_base=job,
        mapping=_INVOICE_MAPPING,
    )


def _reparse(ingestion: IngestionService, file_id: UUID, job: int) -> None:
    ingestion.ingest_file(file_id, job_id=job)
    ingestion.parse_file(file_id, job_id=job + 1)


def test_the_same_bytes_are_one_upload_even_at_the_same_time(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, batch_id = _setup(scratch_engine, "dup-file", tmp_path)
    content = _invoice("INV-DUP-1", date(2026, 9, 5))

    def receive(name: str) -> Any:
        return ingestion.receive_file(
            actor,
            batch_id=batch_id,
            zone=UploadZone.INVOICE,
            display_name=name,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            file_format=FileFormat.CSV,
            stream=io.BytesIO(content),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(receive, ["a.csv", "b.csv"]))
    assert len({receipt.file_id for receipt in receipts}) == 1
    assert _count(scratch_engine, "source_file", workspace_id=actor.workspace_id) == 1
    again = receive("c.csv")
    assert again.duplicate is True
    assert again.original_display_name in {"a.csv", "b.csv"}
    assert again.original_batch_id == batch_id
    assert (
        _count(
            scratch_engine,
            "audit_log",
            workspace_id=actor.workspace_id,
            action="file.duplicate_skipped",
        )
        >= 1
    )


def test_po_reimport_unchanged_is_skipped_and_changed_is_an_amendment(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, batch_id = _setup(scratch_engine, "dup-po", tmp_path)
    ws = actor.workspace_id
    first = _upload_csv(
        ingestion,
        actor,
        batch_id,
        zone=UploadZone.PO,
        display_name="po.csv",
        content=_po("PO-DUP-1", date(2026, 9, 1)),
        job_base=100,
    )
    versions = _count(scratch_engine, "document_version", workspace_id=ws)
    with scratch_engine.connect() as connection:
        connection.execute(sa.text("SELECT 1"))
    # The same export saved again: new bytes, same content.
    second = _upload_csv(
        ingestion,
        actor,
        batch_id,
        zone=UploadZone.PO,
        display_name="po-again.csv",
        content=_po("PO-DUP-1", date(2026, 9, 1), note="\n"),
        job_base=200,
    )
    assert second != first
    state = ingestion.get_file(ws, second)
    assert state is not None
    assert (state["docs_new"], state["docs_updated"], state["docs_skipped"]) == (0, 0, 1)
    assert _count(scratch_engine, "document_version", workspace_id=ws) == versions
    assert _count(scratch_engine, "document", workspace_id=ws, doc_type="po") == 1
    # An amendment (new quantity and date): a new version of the same document.
    third = _upload_csv(
        ingestion,
        actor,
        batch_id,
        zone=UploadZone.PO,
        display_name="po-amended.csv",
        content=_po("PO-DUP-1", date(2026, 9, 3), quantity="6"),
        job_base=300,
    )
    amended = ingestion.get_file(ws, third)
    assert amended is not None
    assert (amended["docs_new"], amended["docs_updated"]) == (0, 1)
    assert _count(scratch_engine, "document", workspace_id=ws, doc_type="po") == 1
    assert _count(scratch_engine, "document_version", workspace_id=ws) == versions + 1
    # Removing the first export keeps the PO alive: the amendment is current.
    ingestion.soft_delete(actor, first)
    document = metadata.tables["document"]
    with scratch_engine.connect() as connection:
        withdrawn = connection.scalar(
            sa.select(document.c.withdrawn_at).where(
                document.c.workspace_id == ws, document.c.doc_type == "po"
            )
        )
    assert withdrawn is None


def test_invoice_duplicate_is_held_then_discarded_or_replaces_the_original(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, batch_id = _setup(scratch_engine, "dup-inv", tmp_path)
    ws = actor.workspace_id
    day = date(2026, 9, 5)
    _upload_invoice(ingestion, actor, batch_id, "inv.csv", _invoice("INV-DUP-7", day), 100)
    [(original_id, _withdrawn)] = _invoices(scratch_engine, ws, "INV-DUP-7")
    versions = _count(scratch_engine, "document_version", document_id=original_id)

    copy_file = _upload_invoice(
        ingestion, actor, batch_id, "inv-copy.csv", _invoice("INV-DUP-7", day, quantity="3"), 200
    )
    assert len(_invoices(scratch_engine, ws, "INV-DUP-7")) == 1
    [hold] = ingestion.list_duplicate_holds(ws, file_id=copy_file)
    assert (hold["kind"], hold["reason"], hold["state"]) == (
        "duplicate",
        "same_vendor_number_year",
        "open",
    )
    assert hold["duplicate_of_document_id"] == original_id
    assert hold["original_file_name"] == "inv.csv"
    assert (
        _count(scratch_engine, "audit_log", workspace_id=ws, action="document.duplicate_held") == 1
    )

    discarded = ingestion.decide_duplicate_hold(
        actor, hold["id"], expected_version=int(hold["version"]), decision="discard"
    )
    assert discarded is not None and discarded["state"] == "discarded"
    _reparse(ingestion, copy_file, 250)  # a later re-read never ingests a discarded copy
    assert len(_invoices(scratch_engine, ws, "INV-DUP-7")) == 1
    assert _count(scratch_engine, "document_version", document_id=original_id) == versions

    replace_file = _upload_invoice(
        ingestion, actor, batch_id, "inv-fixed.csv", _invoice("INV-DUP-7", day, quantity="4"), 300
    )
    [hold2] = ingestion.list_duplicate_holds(ws, file_id=replace_file)
    try:
        ingestion.decide_duplicate_hold(
            actor, hold2["id"], expected_version=int(hold2["version"]), decision="keep"
        )
        raise AssertionError("a certain duplicate cannot be kept as a second document")
    except Exception as exc:  # IngestionFailure
        assert "DUPLICATE_DECISION_INVALID" in repr(exc) or "kept" in str(exc)
    replaced = ingestion.decide_duplicate_hold(
        actor, hold2["id"], expected_version=int(hold2["version"]), decision="replace"
    )
    assert replaced is not None and replaced["state"] == "replaced"
    _reparse(ingestion, replace_file, 350)
    assert [row.id for row in _invoices(scratch_engine, ws, "INV-DUP-7")] == [original_id]
    assert _count(scratch_engine, "document_version", document_id=original_id) == versions + 1
    assert (
        _count(scratch_engine, "audit_log", workspace_id=ws, action="document.duplicate_decide")
        == 2
    )


def test_uncertain_match_is_held_and_can_be_kept_as_new(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, batch_id = _setup(scratch_engine, "dup-possible", tmp_path)
    ws = actor.workspace_id
    day = date(2026, 9, 5)
    _upload_invoice(ingestion, actor, batch_id, "inv.csv", _invoice("INV-DUP-9", day), 100)
    # The same number with no supplier GSTIN: the vendor is not resolved.
    unresolved = _upload_invoice(
        ingestion, actor, batch_id, "inv-nogst.csv", _invoice("INV-DUP-9", day, gstin=""), 200
    )
    [hold] = ingestion.list_duplicate_holds(ws, file_id=unresolved)
    assert (hold["kind"], hold["reason"]) == ("possible", "vendor_not_resolved")
    assert len(_invoices(scratch_engine, ws, "INV-DUP-9")) == 1
    kept = ingestion.decide_duplicate_hold(
        actor, hold["id"], expected_version=int(hold["version"]), decision="keep"
    )
    assert kept is not None and kept["state"] == "kept"
    _reparse(ingestion, unresolved, 250)
    assert len(_invoices(scratch_engine, ws, "INV-DUP-9")) == 2


def test_other_supplier_and_withdrawn_original_are_not_duplicates(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    assert validate_gstin(_OTHER_GSTIN).is_valid
    actor, ingestion, batch_id = _setup(scratch_engine, "dup-not", tmp_path)
    ws = actor.workspace_id
    day = date(2026, 9, 5)
    first = _upload_invoice(ingestion, actor, batch_id, "inv.csv", _invoice("INV-DUP-11", day), 100)
    other = _upload_invoice(
        ingestion,
        actor,
        batch_id,
        "inv-other.csv",
        _invoice("INV-DUP-11", day, gstin=_OTHER_GSTIN, vendor="Other Synthetic Supplier Ltd"),
        200,
    )
    assert ingestion.list_duplicate_holds(ws, file_id=other) == []
    assert len(_invoices(scratch_engine, ws, "INV-DUP-11")) == 2
    # The original file removed: its document is withdrawn and no longer counts.
    ingestion.soft_delete(actor, first)
    again = _upload_invoice(
        ingestion, actor, batch_id, "inv-again.csv", _invoice("INV-DUP-11", day, quantity="5"), 300
    )
    assert ingestion.list_duplicate_holds(ws, file_id=again) == []
    active = [
        row for row in _invoices(scratch_engine, ws, "INV-DUP-11") if row.withdrawn_at is None
    ]
    assert len(active) == 2


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    result = io.BytesIO()
    with zipfile.ZipFile(result, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return result.getvalue()


def test_zip_with_same_file_twice_produces_one_document(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, batch_id = _setup(scratch_engine, "dup-zip", tmp_path)
    ws = actor.workspace_id
    content = _po("PO-ZIP-1", date(2026, 9, 1))
    archive_bytes = _zip_bytes({"po_copy_1.csv": content, "po_copy_2.csv": content})
    receipt = ingestion.receive_file(
        actor,
        batch_id=batch_id,
        zone=UploadZone.PO,
        display_name="archive.zip",
        size_bytes=len(archive_bytes),
        sha256=hashlib.sha256(archive_bytes).hexdigest(),
        file_format=FileFormat.ZIP,
        stream=io.BytesIO(archive_bytes),
    )
    queued = ingestion.queue_files(actor, batch_id=batch_id)
    assert any(item["file_id"] == receipt.file_id for item in queued)
    ingestion.ingest_file(receipt.file_id, job_id=100)
    source = metadata.tables["source_file"]
    with scratch_engine.connect() as connection:
        children = list(
            connection.execute(
                sa.select(source.c.id, source.c.status, source.c.reupload_skips).where(
                    source.c.workspace_id == ws,
                    source.c.parent_file_id == receipt.file_id,
                )
            ).mappings()
        )
    assert len(children) == 1
    assert children[0]["reupload_skips"] == 1
    child_id = children[0]["id"]
    ingestion.parse_file(child_id, job_id=101)
    assert _count(scratch_engine, "document", workspace_id=ws, doc_type="po") == 1
    assert _count(scratch_engine, "document_version", workspace_id=ws) == 1


def test_withdrawn_document_reuploaded_is_ingested(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, batch_id = _setup(scratch_engine, "dup-withdrawn-reupload", tmp_path)
    ws = actor.workspace_id
    day = date(2026, 9, 5)
    content = _invoice("INV-DUP-WITHDRAWN", day)
    first_file = _upload_invoice(ingestion, actor, batch_id, "inv.csv", content, 100)
    invoices = _invoices(scratch_engine, ws, "INV-DUP-WITHDRAWN")
    assert len(invoices) == 1
    assert invoices[0].withdrawn_at is None

    # Soft delete the file -> withdraws the document
    ingestion.soft_delete(actor, first_file)
    with scratch_engine.connect() as connection:
        doc_tab = metadata.tables["document"]
        withdrawn_at = connection.scalar(
            sa.select(doc_tab.c.withdrawn_at).where(
                doc_tab.c.workspace_id == ws, doc_tab.c.id == invoices[0].id
            )
        )
    assert withdrawn_at is not None

    # Re-upload the exact same file
    receipt = ingestion.receive_file(
        actor,
        batch_id=batch_id,
        zone=UploadZone.INVOICE,
        display_name="inv-reactivated.csv",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        file_format=FileFormat.CSV,
        stream=io.BytesIO(content),
    )
    assert receipt.file_id == first_file
    assert receipt.duplicate is False
    ingestion.queue_files(actor, batch_id=batch_id)
    ingestion.ingest_file(receipt.file_id, job_id=200)
    ingestion.parse_file(receipt.file_id, job_id=201)
    file_state = ingestion.get_file(ws, receipt.file_id)
    assert file_state is not None
    if file_state["error_code"] == "CSV_MAPPING_REQUIRED":
        ingestion.save_csv_mapping(actor, receipt.file_id, column_mapping=_INVOICE_MAPPING)
        ingestion.ingest_file(receipt.file_id, job_id=202)
        ingestion.parse_file(receipt.file_id, job_id=203)
    active_invoices = [
        row
        for row in _invoices(scratch_engine, ws, "INV-DUP-WITHDRAWN")
        if row.withdrawn_at is None
    ]
    assert len(active_invoices) == 1
    assert active_invoices[0].id == invoices[0].id

