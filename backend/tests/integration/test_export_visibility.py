"""Disposable-database tests for the export's "Not included" and "PO Balance" loaders."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from reconbench_app.db.metadata import metadata
from reconbench_app.export_service import ExportService
from reconbench_app.ledger_service import LedgerRebuildService
from reconbench_app.vendor_resolution import VendorResolutionService
from sqlalchemy.engine import Engine
from tests.integration.test_pd_ledger_scopes import _bundle, _workspace
from tests.integration.test_phase4_task_411_413 import scratch_engine

__all__ = ["scratch_engine"]


def test_loaders_list_unreconciled_documents_files_and_po_ledger_lines(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    engine = scratch_engine
    actor, ingestion = _workspace(engine, "export-visibility", tmp_path)
    workspace_id = actor.workspace_id
    batch_id = UUID(str(ingestion.create_batch(actor, label="export-visibility")["id"]))
    files = _bundle(engine, actor, ingestion, batch_id, "PO-EV-1", 100)
    VendorResolutionService(engine).resolve_workspace(workspace_id)
    ledger = LedgerRebuildService(engine)
    while ledger.drain_dirty_scopes(workspace_id, sweep=True)["processed"]:
        pass

    document = metadata.tables["document"]
    source_file = metadata.tables["source_file"]
    with engine.begin() as connection:
        connection.execute(
            sa.update(document)
            .where(
                document.c.workspace_id == workspace_id,
                document.c.doc_type == "invoice",
            )
            .values(reading_state="failed", reading_failure_code="OUTPUT_CAP")
        )
        connection.execute(
            sa.update(source_file)
            .where(source_file.c.id == UUID(str(files["grn"])))
            .values(status="failed", error_code="PARSE_FAILED")
        )

    with engine.connect() as connection:
        missing = ExportService._not_included_rows(connection, workspace_id)
        balance = ExportService._po_balance_rows(connection, workspace_id)

    assert [(r["kind"], r["doc_type"], r["state"], r["code"]) for r in missing] == [
        ("Document", "invoice", "failed", "OUTPUT_CAP"),
        ("File", None, "failed", "PARSE_FAILED"),
    ]
    assert missing[0]["file_name"] == "INV-PO-EV-1.csv"
    assert missing[1]["file_name"] == "GRN-PO-EV-1.csv"
    assert balance
    assert {row["po_no"] for row in balance} == {"PO-EV-1"}
    assert all(isinstance(row["open_qty"], Decimal) for row in balance)

    # A withdrawn PO drops out of the balance sheet.
    with engine.begin() as connection:
        connection.execute(
            sa.update(document)
            .where(document.c.workspace_id == workspace_id, document.c.doc_type == "po")
            .values(withdrawn_at=sa.func.now())
        )
    with engine.connect() as connection:
        assert ExportService._po_balance_rows(connection, workspace_id) == []
