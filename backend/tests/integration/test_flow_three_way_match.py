"""End-to-end user flows on a disposable database: upload → vendor → link → ledger → run.

Covers the three problems seen on the live test (3 Nov 2026): no case ever reached
Matched, a person's Verify left links suggested, and a zero-amount row failed the case.
Synthetic documents only; nothing touches a live database or an AI provider.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from reconbench_app.authorization import ActorContext
from reconbench_app.db.metadata import metadata
from reconbench_app.ingestion import IngestionService
from reconbench_app.ledger_service import LedgerRebuildService
from reconbench_app.mapping_service import MappingService
from reconbench_app.reconciliation_service import ReconciliationService
from reconbench_app.storage import LocalObjectStore
from reconbench_app.vendor_resolution import VendorResolutionService
from reconbench_core.schema import UploadZone
from sqlalchemy.engine import Engine
from tests.integration.test_pd4d_verify_ai_values import _copy_version
from tests.integration.test_phase4_task_411_413 import (
    _GSTIN,
    _HSN,
    _VENDOR_NAME,
    _create_queued_run,
    _csv_bytes,
    _grn_csv,
    _po_csv,
    _Queue,
    _seed_workspace,
    _upload_csv,
    scratch_engine,
)

__all__ = ["scratch_engine"]

_GRN_MAP = {"PO Ref": "po_refs", "GRN No": "doc_no", "GRN Date": "doc_date"}
_INVOICE_MAP = {"PO Ref": "po_refs", "Invoice No": "doc_no", "Invoice Date": "doc_date"}


def _invoice_csv(invoice_no: str, po_no: str, *, zero_row: bool = False) -> bytes:
    head = [invoice_no, "15/09/2026", po_no, _VENDOR_NAME, _GSTIN, "INR", "200", "236", "18", "18"]
    rows = [[*head, "1", "Precision Widget PW-100", _HSN, "NOS", "2", "100", "200"]]
    if zero_row:
        rows.append([*head, "2", "Shipping charges", "", "NOS", "1", "0", "0"])
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
        rows,
    )


def _upload_set(
    engine: Engine, tmp_path: Path, label: str, *, zero_row: bool = False
) -> tuple[ActorContext, IngestionService, UUID, _Queue]:
    workspace_id, actor = _seed_workspace(engine, label)
    queue = _Queue()
    ingestion = IngestionService(engine, LocalObjectStore(tmp_path / label), queue)
    batch = UUID(str(ingestion.create_batch(actor, label=f"Synthetic {label}")["id"]))
    _upload_csv(
        ingestion,
        actor,
        batch,
        zone=UploadZone.PO,
        display_name="po.csv",
        content=_po_csv("PO-FLOW-1", date(2026, 9, 1)),
        job_base=10,
    )
    _upload_csv(
        ingestion,
        actor,
        batch,
        zone=UploadZone.GRN,
        display_name="grn.csv",
        content=_grn_csv("GRN-FLOW-1", date(2026, 9, 10), "PO-FLOW-1"),
        job_base=20,
        mapping=_GRN_MAP,
    )
    _upload_csv(
        ingestion,
        actor,
        batch,
        zone=UploadZone.INVOICE,
        display_name="invoice.csv",
        content=_invoice_csv("INV-FLOW-1", "PO-FLOW-1", zero_row=zero_row),
        job_base=30,
        mapping=_INVOICE_MAP,
    )
    assert workspace_id == actor.workspace_id
    return actor, ingestion, _document_id(engine, actor, "invoice"), queue


def _document_id(engine: Engine, actor: ActorContext, doc_type: str) -> UUID:
    document = metadata.tables["document"]
    with engine.connect() as connection:
        return UUID(
            str(
                connection.execute(
                    sa.select(document.c.id).where(
                        document.c.workspace_id == actor.workspace_id,
                        document.c.doc_type == doc_type,
                    )
                ).scalar_one()
            )
        )


def _match_and_run(engine: Engine, actor: ActorContext) -> Any:
    """What the worker does after an upload or a person's change, then a person's Run."""

    VendorResolutionService(engine).resolve_workspace(actor.workspace_id)
    MappingService(engine).resolve_workspace(actor.workspace_id)
    LedgerRebuildService(engine).rebuild_po(actor.workspace_id, _document_id(engine, actor, "po"))
    run_id = _create_queued_run(engine, actor.workspace_id, actor.actor_id)
    [case] = ReconciliationService(engine).evaluate_run(run_id)
    return case


def _invoice_po_link_states(engine: Engine, actor: ActorContext) -> list[str]:
    link, current = metadata.tables["link"], metadata.tables["link_current"]
    with engine.connect() as connection:
        return sorted(
            str(state)
            for state in connection.scalars(
                sa.select(link.c.state)
                .select_from(current.join(link, current.c.current_link_id == link.c.id))
                .where(
                    link.c.workspace_id == actor.workspace_id,
                    link.c.link_type == "inv_po",
                    link.c.state != "rejected",
                )
            )
        )


def test_a_clean_digital_three_way_set_is_matched_and_auto_linked(
    scratch_engine: Engine, tmp_path: Path
) -> None:
    actor, _ingestion, _invoice_id, _queue = _upload_set(scratch_engine, tmp_path, "flow-digital")

    case = _match_and_run(scratch_engine, actor)

    assert _invoice_po_link_states(scratch_engine, actor) == ["auto"]
    assert case.status.value == "matched", case.reason_codes


def test_a_zero_amount_shipping_row_does_not_stop_the_match(
    scratch_engine: Engine, tmp_path: Path
) -> None:
    actor, _ingestion, _invoice_id, _queue = _upload_set(
        scratch_engine, tmp_path, "flow-zero-row", zero_row=True
    )

    case = _match_and_run(scratch_engine, actor)

    assert "ITEM_NOT_ON_PO" not in {code.value for code in case.reason_codes}
    assert case.status.value == "matched", case.reason_codes


def test_an_ai_read_invoice_matches_after_a_person_verifies_its_values(
    scratch_engine: Engine, tmp_path: Path
) -> None:
    actor, ingestion, invoice_id, queue = _upload_set(scratch_engine, tmp_path, "flow-ai")
    # The same invoice read as a scan: a rules (OCR) copy, then the AI reading leads.
    _copy_version(scratch_engine, invoice_id, method="pdf_ocr", ai=False)
    _copy_version(scratch_engine, invoice_id, method="ai_first", ai=True)

    held = _match_and_run(scratch_engine, actor)
    assert _invoice_po_link_states(scratch_engine, actor) == ["suggested"]
    assert held.status.value == "needs_review"
    assert "AI_VALUE_UNVERIFIED" in {code.value for code in held.reason_codes}

    details = ingestion.get_document(actor.workspace_id, invoice_id)
    assert details is not None
    jobs_before = len(queue.jobs)
    ingestion.verify_ai_values(
        actor,
        invoice_id,
        expected_version=int(details["document"]["version"]),
        all_agreeing=True,
    )
    # The Verify queued vendor and link matching again (the worker runs it next).
    assert [name for name, _file in queue.jobs[jobs_before:]] == ["resolve_vendors"]

    matched = _match_and_run(scratch_engine, actor)
    assert _invoice_po_link_states(scratch_engine, actor) == ["auto"]
    assert matched.status.value == "matched", matched.reason_codes
