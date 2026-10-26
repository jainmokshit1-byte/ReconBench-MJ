"""Disposable-database tests for PD-0b-1 (withdrawn POs in dirty scopes) and PD-0b-3 (sweep)."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from reconbench_app.authorization import ActorContext
from reconbench_app.db.metadata import metadata
from reconbench_app.ingestion import IngestionService
from reconbench_app.ledger_service import LedgerRebuildService
from reconbench_app.mapping_service import MappingService
from reconbench_app.storage import LocalObjectStore
from reconbench_app.vendor_resolution import VendorResolutionService
from reconbench_core.schema import UploadZone
from sqlalchemy.engine import Engine
from tests.integration.test_phase4_task_411_413 import (
    _grn_csv,
    _invoice_csv_rows,
    _po_csv,
    _Queue,
    _seed_workspace,
    _upload_csv,
    scratch_engine,
)

__all__ = ["scratch_engine"]

_GRN_MAPPING = {"PO Ref": "po_refs", "GRN No": "doc_no", "GRN Date": "doc_date"}
_INVOICE_MAPPING = {"PO Ref": "po_refs", "Invoice No": "doc_no", "Invoice Date": "doc_date"}


def _po_id(engine: Engine, workspace_id: UUID, po_no: str) -> UUID:
    document = metadata.tables["document"]
    with engine.connect() as connection:
        return UUID(
            str(
                connection.scalar(
                    sa.select(document.c.id).where(
                        document.c.workspace_id == workspace_id,
                        document.c.doc_type == "po",
                        document.c.doc_no == po_no,
                    )
                )
            )
        )


def _bundle(
    engine: Engine,
    actor: ActorContext,
    ingestion: IngestionService,
    batch_id: UUID,
    po_no: str,
    job_base: int,
) -> dict[str, UUID]:
    day = date(2026, 9, 1)
    files = {
        "po": _upload_csv(
            ingestion,
            actor,
            batch_id,
            zone=UploadZone.PO,
            display_name=f"{po_no}.csv",
            content=_po_csv(po_no, day),
            job_base=job_base,
        ),
        "grn": _upload_csv(
            ingestion,
            actor,
            batch_id,
            zone=UploadZone.GRN,
            display_name=f"GRN-{po_no}.csv",
            content=_grn_csv(f"GRN-{po_no}", day.replace(day=10), po_no),
            job_base=job_base + 10,
            mapping=_GRN_MAPPING,
        ),
        "invoice": _upload_csv(
            ingestion,
            actor,
            batch_id,
            zone=UploadZone.INVOICE,
            display_name=f"INV-{po_no}.csv",
            content=_invoice_csv_rows(
                invoice_no=f"INV-{po_no}", invoice_date=day.replace(day=12), po_no=po_no
            ),
            job_base=job_base + 20,
            mapping=_INVOICE_MAPPING,
        ),
    }
    return files


def _workspace(engine: Engine, label: str, tmp_path: Any) -> tuple[ActorContext, IngestionService]:
    workspace_id, actor = _seed_workspace(engine, label)
    del workspace_id
    ingestion = IngestionService(engine, LocalObjectStore(tmp_path / label), _Queue())
    return actor, ingestion


def _ledger_state(connection: sa.Connection, workspace_id: UUID) -> list[tuple[Any, ...]]:
    """Ledger rows and entries without generated ids or timestamps."""

    ledger = metadata.tables["po_line_ledger"]
    entry = metadata.tables["ledger_entry"]
    rows = connection.execute(
        sa.select(
            ledger.c.po_document_id,
            ledger.c.po_line_no,
            ledger.c.ordered,
            ledger.c.received_accepted,
            ledger.c.received_rejected,
            ledger.c.billed,
            ledger.c.input_hash,
            entry.c.kind,
            entry.c.seq,
            entry.c.source_document_id,
            entry.c.accepted_delta,
            entry.c.rejected_delta,
            entry.c.billed_delta,
            entry.c.consumes,
            entry.c.excluded_reason,
        )
        .select_from(ledger.outerjoin(entry, entry.c.po_line_ledger_id == ledger.c.id))
        .where(ledger.c.workspace_id == workspace_id)
    ).all()
    return sorted(tuple(str(item) for item in row) for row in rows)


def _full_recompute_state(engine: Engine, workspace_id: UUID) -> list[tuple[Any, ...]]:
    """What a full recompute would store, computed inside a rolled-back transaction."""

    service = LedgerRebuildService(engine)
    document = metadata.tables["document"]
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            sa.delete(metadata.tables["po_line_ledger"]).where(
                metadata.tables["po_line_ledger"].c.workspace_id == workspace_id
            )
        )
        for po_id in connection.scalars(
            sa.select(document.c.id).where(
                document.c.workspace_id == workspace_id,
                document.c.doc_type == "po",
                document.c.withdrawn_at.is_(None),
                document.c.current_version_id.is_not(None),
            )
        ).all():
            service.rebuild_po_connection(connection, workspace_id, UUID(str(po_id)))
        return _ledger_state(connection, workspace_id)
    finally:
        transaction.rollback()
        connection.close()


def _stored_state(engine: Engine, workspace_id: UUID) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return _ledger_state(connection, workspace_id)


def _open_scopes(engine: Engine, workspace_id: UUID) -> set[tuple[str, UUID]]:
    scopes = metadata.tables["dirty_scope"]
    with engine.connect() as connection:
        return {
            (str(row.scope_kind), UUID(str(row.scope_id)))
            for row in connection.execute(
                sa.select(scopes.c.scope_kind, scopes.c.scope_id).where(
                    scopes.c.workspace_id == workspace_id, scopes.c.processed_at.is_(None)
                )
            )
        }


def _ledger_po_ids(engine: Engine, workspace_id: UUID) -> set[UUID]:
    ledger = metadata.tables["po_line_ledger"]
    with engine.connect() as connection:
        return {
            UUID(str(item))
            for item in connection.scalars(
                sa.select(ledger.c.po_document_id).where(ledger.c.workspace_id == workspace_id)
            )
        }


def test_withdrawn_po_in_a_dirty_scope_is_processed_without_error(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    engine = scratch_engine
    actor, ingestion = _workspace(engine, "withdrawn-po", tmp_path)
    workspace_id = actor.workspace_id
    batch_id = UUID(str(ingestion.create_batch(actor, label="PD-0b-1")["id"]))
    first = _bundle(engine, actor, ingestion, batch_id, "PO-PD-101", 100)
    _bundle(engine, actor, ingestion, batch_id, "PO-PD-102", 200)
    VendorResolutionService(engine).resolve_workspace(workspace_id)
    MappingService(engine).resolve_workspace(workspace_id)
    ledger = LedgerRebuildService(engine)
    while ledger.drain_dirty_scopes(workspace_id, sweep=True)["processed"]:
        pass
    withdrawn_po = _po_id(engine, workspace_id, "PO-PD-101")
    active_po = _po_id(engine, workspace_id, "PO-PD-102")
    assert {withdrawn_po, active_po} <= _ledger_po_ids(engine, workspace_id)
    active_before = [row for row in _stored_state(engine, workspace_id) if row[0] == str(active_po)]

    # Removing the PO file withdraws the PO and marks its scope dirty (as on live).
    ingestion.soft_delete(actor, first["po"])
    assert ("po", withdrawn_po) in _open_scopes(engine, workspace_id)
    with pytest.raises(ValueError):
        # The old rule still applies to a single rebuild of a document that is no PO at all.
        ledger.rebuild_po(workspace_id, UUID(int=0))

    result = ledger.drain_dirty_scopes(workspace_id, sweep=True)

    assert result["failed"] == 0
    assert result["processed"] == result["scopes"] >= 1
    assert ("po", withdrawn_po) not in _open_scopes(engine, workspace_id)
    assert withdrawn_po not in _ledger_po_ids(engine, workspace_id)
    active_after = [row for row in _stored_state(engine, workspace_id) if row[0] == str(active_po)]
    assert active_after == active_before
    assert _stored_state(engine, workspace_id) == _full_recompute_state(engine, workspace_id)
    # A single rebuild of the withdrawn PO (an old queued job) no longer raises either.
    assert ledger.rebuild_po(workspace_id, withdrawn_po) == {
        "lines": 0,
        "entries": 0,
        "changed": False,
    }


def test_one_failing_po_never_rolls_back_the_other_scopes(
    scratch_engine: Engine, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = scratch_engine
    actor, ingestion = _workspace(engine, "isolated-po", tmp_path)
    workspace_id = actor.workspace_id
    batch_id = UUID(str(ingestion.create_batch(actor, label="PD-0b-1 isolation")["id"]))
    _bundle(engine, actor, ingestion, batch_id, "PO-PD-201", 300)
    _bundle(engine, actor, ingestion, batch_id, "PO-PD-202", 400)
    bad_po = _po_id(engine, workspace_id, "PO-PD-201")
    good_po = _po_id(engine, workspace_id, "PO-PD-202")
    assert {("po", bad_po), ("po", good_po)} <= _open_scopes(engine, workspace_id)

    ledger = LedgerRebuildService(engine)
    original = LedgerRebuildService.rebuild_po_connection

    def failing(
        self: LedgerRebuildService, connection: sa.Connection, ws: UUID, po: UUID
    ) -> dict[str, int | bool]:
        if po == bad_po:
            original(self, connection, ws, po)  # writes, then fails: the savepoint undoes it
            raise ValueError("synthetic failure")
        return original(self, connection, ws, po)

    monkeypatch.setattr(LedgerRebuildService, "rebuild_po_connection", failing)
    result = ledger.drain_dirty_scopes(workspace_id, sweep=True)

    assert result["failed"] == 1
    assert ("po", bad_po) in _open_scopes(engine, workspace_id)
    assert ("po", good_po) not in _open_scopes(engine, workspace_id)
    assert good_po in _ledger_po_ids(engine, workspace_id)
    assert bad_po not in _ledger_po_ids(engine, workspace_id)
    # The next batch claims only the failing scope, processes nothing, and stops the loop.
    again = ledger.drain_dirty_scopes(workspace_id)
    assert again["processed"] == 0 and again["failed"] == 1

    monkeypatch.setattr(LedgerRebuildService, "rebuild_po_connection", original)
    assert ledger.drain_dirty_scopes(workspace_id)["processed"] >= 1
    assert _open_scopes(engine, workspace_id) == set()
    assert _stored_state(engine, workspace_id) == _full_recompute_state(engine, workspace_id)


class _RecordingQueue(_Queue):
    def __init__(self) -> None:
        super().__init__()
        self.drains = 0

    def enqueue_pending_recomputes(self, *, workspace_id: UUID, connection: sa.Connection) -> int:
        self.drains += 1
        return super().enqueue_pending_recomputes(workspace_id=workspace_id, connection=connection)


def test_unmarked_input_change_queues_a_drain_that_equals_a_full_recompute(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    engine = scratch_engine
    workspace_id, actor = _seed_workspace(engine, "sweep")
    queue = _RecordingQueue()
    ingestion = IngestionService(engine, LocalObjectStore(tmp_path / "sweep"), queue)
    batch_id = UUID(str(ingestion.create_batch(actor, label="PD-0b-3")["id"]))
    files = _bundle(engine, actor, ingestion, batch_id, "PO-PD-301", 500)
    VendorResolutionService(engine).resolve_workspace(workspace_id)
    MappingService(engine).resolve_workspace(workspace_id)
    ledger = LedgerRebuildService(engine)
    while ledger.drain_dirty_scopes(workspace_id, sweep=True)["processed"]:
        pass
    assert _stored_state(engine, workspace_id) == _full_recompute_state(engine, workspace_id)
    before = _stored_state(engine, workspace_id)

    # Removing the invoice file changes the PO's inputs without marking any scope;
    # it now queues the drain job, whose sweep brings the ledger to the full recompute.
    drains = queue.drains
    ingestion.soft_delete(actor, files["invoice"])
    assert queue.drains == drains + 1
    assert _stored_state(engine, workspace_id) != _full_recompute_state(engine, workspace_id)
    ledger.drain_dirty_scopes(workspace_id, sweep=True)
    assert _stored_state(engine, workspace_id) == _full_recompute_state(engine, workspace_id)
    assert _stored_state(engine, workspace_id) != before

    # A sweep on an up-to-date workspace writes nothing.
    with engine.begin() as connection:
        assert ledger.sweep_connection(connection, workspace_id)["changed"] == 0
