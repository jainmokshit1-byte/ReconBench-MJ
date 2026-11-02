"""Disposable-database tests: a person's vendor change re-runs vendor and link matching safely."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from reconbench_app.authorization import ActorContext
from reconbench_app.db.metadata import metadata
from reconbench_app.ingestion import IngestionService
from reconbench_app.jobs import run_vendor_rematch
from reconbench_app.mapping_service import MappingService
from reconbench_app.storage import LocalObjectStore
from reconbench_app.vendor_resolution import VendorResolutionService
from reconbench_core.schema import UploadZone
from sqlalchemy.engine import Engine
from tests.integration.test_phase4_task_411_413 import (
    _csv_bytes,
    _Queue,
    _seed_workspace,
    _upload_csv,
    scratch_engine,
)

__all__ = ["scratch_engine"]

_INVOICE_MAPPING = {"PO Ref": "po_refs", "Invoice No": "doc_no", "Invoice Date": "doc_date"}
_DAY = date(2026, 9, 1)


class _RecordingQueue(_Queue):
    def __init__(self) -> None:
        super().__init__()
        self.rematches: list[UUID] = []

    def enqueue_vendor_rematch(self, *, workspace_id: UUID, connection: sa.Connection) -> int:
        assert connection.in_transaction()
        self.rematches.append(workspace_id)
        return len(self.rematches)

    def enqueue_pending_recomputes(self, *, workspace_id: UUID, connection: sa.Connection) -> int:
        return self.enqueue_dirty_scopes(workspace_id=workspace_id, connection=connection)


def _po(po_no: str, vendor: str, gstin: str) -> bytes:
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
                _DAY.strftime("%d/%m/%Y"),
                vendor,
                gstin,
                "INR",
                "400",
                "400",
                "open",
                "1",
                "Repair service visit",
                "998713",
                "NOS",
                "1",
                "400",
                "400",
            ]
        ],
    )


def _invoice(invoice_no: str, vendor: str, gstin: str, po_ref: str) -> bytes:
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
                _DAY.replace(day=12).strftime("%d/%m/%Y"),
                po_ref,
                vendor,
                gstin,
                "INR",
                "400",
                "400",
                "0",
                "0",
                "1",
                "Repair service visit",
                "998713",
                "NOS",
                "1",
                "400",
                "400",
            ]
        ],
    )


def _upload(
    ingestion: IngestionService,
    actor: ActorContext,
    zone: UploadZone,
    name: str,
    content: bytes,
    job_base: int,
) -> None:
    batch_id = UUID(str(ingestion.create_batch(actor, label=name)["id"]))
    _upload_csv(
        ingestion,
        actor,
        batch_id,
        zone=zone,
        display_name=name,
        content=content,
        job_base=job_base,
        mapping=_INVOICE_MAPPING if zone is UploadZone.INVOICE else None,
    )


def _vendors_by_doc(engine: Engine, workspace_id: UUID) -> dict[str, tuple[Any, ...]]:
    document = metadata.tables["document"]
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(
                document.c.doc_no,
                document.c.vendor_id,
                document.c.vendor_rule,
                document.c.vendor_locked,
            ).where(document.c.workspace_id == workspace_id, document.c.withdrawn_at.is_(None))
        ).all()
    return {str(row.doc_no): (row.vendor_id, row.vendor_rule, row.vendor_locked) for row in rows}


def _doc_id(engine: Engine, workspace_id: UUID, doc_no: str) -> UUID:
    document = metadata.tables["document"]
    with engine.connect() as connection:
        return UUID(
            str(
                connection.scalar(
                    sa.select(document.c.id).where(
                        document.c.workspace_id == workspace_id,
                        document.c.doc_no == doc_no,
                        document.c.withdrawn_at.is_(None),
                    )
                )
            )
        )


def _invoice_po_links(engine: Engine, workspace_id: UUID, invoice_id: UUID) -> list[str]:
    link, pointer = metadata.tables["link"], metadata.tables["link_current"]
    with engine.connect() as connection:
        return [
            str(state)
            for state in connection.scalars(
                sa.select(link.c.state)
                .select_from(pointer.join(link, link.c.id == pointer.c.current_link_id))
                .where(
                    pointer.c.workspace_id == workspace_id,
                    link.c.from_document_id == invoice_id,
                    link.c.link_type == "inv_po",
                    link.c.state != "rejected",
                )
            )
        ]


def test_vendor_changes_queue_one_rematch_and_the_rematch_is_safe(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    engine = scratch_engine
    workspace_id, actor = _seed_workspace(engine, "vendor-rematch")
    ingestion = IngestionService(engine, LocalObjectStore(tmp_path / "rematch"), _Queue())
    # Name-only supplier (no GSTIN): an invoice and its PO, like an unregistered repair shop.
    _upload(
        ingestion, actor, UploadZone.PO, "solo-po.csv", _po("PO-SOLO-1", "Solo Repairs", ""), 100
    )
    _upload(
        ingestion,
        actor,
        UploadZone.INVOICE,
        "solo-inv.csv",
        _invoice("INV-SOLO-1", "Solo Repairs", "", "PO-SOLO-1"),
        110,
    )
    # Two different registered suppliers with the same name, and a name-only PO for that name.
    _upload(
        ingestion,
        actor,
        UploadZone.PO,
        "bazaar-a.csv",
        _po("PO-BAZ-A", "Bazaar", "27AAPFU0939F1ZV"),
        120,
    )
    _upload(
        ingestion,
        actor,
        UploadZone.PO,
        "bazaar-b.csv",
        _po("PO-BAZ-B", "Bazaar", "29AAACB2894G1ZJ"),
        130,
    )
    _upload(ingestion, actor, UploadZone.PO, "bazaar-x.csv", _po("PO-BAZ-X", "Bazaar", ""), 140)
    VendorResolutionService(engine).resolve_workspace(workspace_id)
    MappingService(engine).resolve_workspace(workspace_id)

    before = _vendors_by_doc(engine, workspace_id)
    assert before["PO-SOLO-1"][0] is None and before["INV-SOLO-1"][0] is None
    assert before["PO-BAZ-X"][0] is None
    assert before["PO-BAZ-A"][0] is not None and before["PO-BAZ-B"][0] is not None
    assert before["PO-BAZ-A"][0] != before["PO-BAZ-B"][0]

    queue = _RecordingQueue()
    service = MappingService(engine, queue)
    # A person creates the supplier and assigns only the invoice (as on live, 1 Nov).
    solo = service.create_vendor(actor, name="Solo Repairs")
    invoice_id = _doc_id(engine, workspace_id, "INV-SOLO-1")
    service.assign_document_vendor(actor, invoice_id, solo["id"])
    # Each vendor change queues the rematch in its own transaction; the real queue keeps one
    # waiting job per workspace (unit test_vendor_rematch_reuses_the_waiting_job_for_the_workspace).
    assert queue.rematches == [workspace_id, workspace_id]
    # Assigning again to the same vendor changes nothing and queues nothing.
    service.assign_document_vendor(actor, invoice_id, solo["id"])
    assert queue.rematches == [workspace_id, workspace_id]

    after_assign = _vendors_by_doc(engine, workspace_id)
    # The assignment touched only the invoice.
    assert {doc for doc in before if before[doc] != after_assign[doc]} == {"INV-SOLO-1"}
    assert after_assign["INV-SOLO-1"] == (solo["id"], "manual", True)
    assert _invoice_po_links(engine, workspace_id, invoice_id) == []

    run_vendor_rematch(
        engine,
        vendor_resolution=VendorResolutionService(engine),
        mapping_service=MappingService(engine, queue),
        queue=queue,
        workspace_id=workspace_id,
    )

    after = _vendors_by_doc(engine, workspace_id)
    # The PO with exactly the same supplier name now belongs to the new vendor.
    assert after["PO-SOLO-1"][:2] == (solo["id"], "name_exact")
    # The person's assignment is kept; the ambiguous name stays unassigned; nothing else moved.
    assert after["INV-SOLO-1"] == (solo["id"], "manual", True)
    assert after["PO-BAZ-X"][0] is None
    assert {doc for doc in after if after[doc] != after_assign[doc]} == {"PO-SOLO-1"}
    # The invoice's PO link appears, never confirmed by the system.
    states = _invoice_po_links(engine, workspace_id, invoice_id)
    assert states and "confirmed" not in states
    audit = metadata.tables["audit_log"]
    with engine.connect() as connection:
        assigns = connection.scalar(
            sa.select(sa.func.count())
            .select_from(audit)
            .where(audit.c.workspace_id == workspace_id, audit.c.action == "vendor.assign")
        )
    assert assigns == 1  # the rematch writes no person's actions


def test_rename_and_merge_queue_a_rematch(scratch_engine: Engine, tmp_path: Any) -> None:
    engine = scratch_engine
    workspace_id, actor = _seed_workspace(engine, "vendor-rematch-rename")
    del tmp_path
    queue = _RecordingQueue()
    service = MappingService(engine, queue)
    first = service.create_vendor(actor, name="Corner Supplies")
    second = service.create_vendor(actor, name="Corner Supply Co")
    assert len(queue.rematches) == 2
    renamed = service.rename_vendor(
        actor, first["id"], name="Corner Supplies Ltd", expected_version=int(first["version"])
    )
    assert renamed is not None and len(queue.rematches) == 3
    merged = service.merge_vendors(
        actor, source_vendor_id=second["id"], survivor_vendor_id=first["id"]
    )
    assert merged is not None and len(queue.rematches) == 4
    assert set(queue.rematches) == {workspace_id}
