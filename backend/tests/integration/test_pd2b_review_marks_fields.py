"""Disposable-database tests for PD-2b (P2): "Save and mark reviewed" verifies what was shown.

Marking an invoice reviewed verifies, as that person, each key value the gate reads that
the person was shown, and the references as before; never a value read by AI. Every
invoice here is synthetic and nothing touches a live database.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from reconbench_app.authorization import ActorContext
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.ingestion import INVOICE_HEADER_COLUMNS, INVOICE_LINE_COLUMNS
from sqlalchemy.engine import Engine
from tests.integration.test_pd_no_duplicates import _invoice, _setup, _upload_invoice
from tests.integration.test_phase4_task_411_413 import scratch_engine

__all__ = ["scratch_engine"]


def _upload(engine: Engine, label: str, tmp_path: Any) -> tuple[ActorContext, Any, UUID]:
    actor, ingestion, batch_id = _setup(engine, label, tmp_path)
    _upload_invoice(
        ingestion, actor, batch_id, f"{label}.csv", _invoice("INV-PD2B-1", date(2026, 9, 5)), 10
    )
    document = metadata.tables["document"]
    with engine.connect() as connection:
        document_id = connection.scalar(
            sa.select(document.c.id).where(
                document.c.workspace_id == actor.workspace_id,
                document.c.doc_no == "INV-PD2B-1",
            )
        )
    assert document_id is not None
    return actor, ingestion, document_id


def _current(engine: Engine, document_id: UUID) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    document = metadata.tables["document"]
    version = metadata.tables["document_version"]
    line = metadata.tables["doc_line"]
    with engine.connect() as connection:
        row = connection.execute(
            sa.select(document.c.version, document.c.current_version_id).where(
                document.c.id == document_id
            )
        ).one()
        header = dict(
            connection.execute(sa.select(version).where(version.c.id == row.current_version_id))
            .mappings()
            .one()
        )
        lines = [
            dict(item)
            for item in connection.execute(
                sa.select(line)
                .where(line.c.document_version_id == row.current_version_id)
                .order_by(line.c.line_no)
            ).mappings()
        ]
    return int(row.version), header, lines


def _shown(
    header: dict[str, Any],
    lines: list[dict[str, Any]],
    header_fields: set[str],
    line_fields: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The patch a screen sends for these fields, each with its current value (no change)."""

    header_patch = {name: header[INVOICE_HEADER_COLUMNS[name]] for name in header_fields}
    if "po_refs" in header_patch:
        header_patch["po_refs"] = list(header_patch["po_refs"] or [])
    lines_patch = [
        {
            "id": line["id"],
            "line_no": line["line_no"],
            **{
                name: line["uom_as_read" if name == "uom" else INVOICE_LINE_COLUMNS[name]]
                for name in line_fields
            },
        }
        for line in lines
    ]
    return header_patch, lines_patch


def _evidence(engine: Engine, document_id: UUID) -> dict[str, list[dict[str, Any]]]:
    """The current version's evidence rows by value name, oldest first."""

    _version, header, lines = _current(engine, document_id)
    evidence = metadata.tables["field_evidence"]
    line_no = {line["id"]: line["line_no"] for line in lines}
    rows: dict[str, list[dict[str, Any]]] = {}
    with engine.connect() as connection:
        for item in connection.execute(
            sa.select(evidence)
            .where(
                sa.or_(
                    evidence.c.document_version_id == header["id"],
                    evidence.c.doc_line_id.in_(list(line_no)),
                )
            )
            .order_by(evidence.c.created_at, evidence.c.id)
        ).mappings():
            owner = item["doc_line_id"]
            name = (
                str(item["field_name"])
                if owner is None
                else f"line {line_no[owner]} {item['field_name']}"
            )
            rows.setdefault(name, []).append(dict(item))
    return rows


def _verification(engine: Engine, document_id: UUID) -> dict[str, str]:
    return {name: rows[-1]["verification"] for name, rows in _evidence(engine, document_id).items()}


def _audit_after(engine: Engine, document_id: UUID) -> dict[str, Any]:
    audit = metadata.tables["audit_log"]
    with engine.connect() as connection:
        state = connection.scalar(
            sa.select(audit.c.after_state)
            .where(audit.c.action == "document.correct", audit.c.target_id == str(document_id))
            .order_by(audit.c.occurred_at.desc(), audit.c.id.desc())
            .limit(1)
        )
    assert isinstance(state, dict)
    return state


def _append(engine: Engine, row: dict[str, Any], **update: Any) -> None:
    """Append a later state of one value, the way a verification is stored."""

    evidence = metadata.tables["field_evidence"]
    copy = {**row, **update, "id": new_id()}
    copy.pop("created_at")
    with engine.begin() as connection:
        connection.execute(sa.insert(evidence).values(**copy))


def test_mark_reviewed_verifies_each_key_value_shown_and_the_references(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _upload(scratch_engine, "pd2b-shown", tmp_path)
    version, header, lines = _current(scratch_engine, document_id)
    before = _verification(scratch_engine, document_id)
    # vendor_gstin and the line amount are not on this screen.
    header_patch, lines_patch = _shown(
        header,
        lines,
        {"doc_no", "doc_date", "vendor_name", "grand_total", "currency", "po_refs"},
        {"description", "quantity", "rate"},
    )

    ingestion.correct_invoice_document(
        actor,
        document_id,
        expected_version=version,
        header_patch=header_patch,
        lines_patch=lines_patch,
        verified=True,
        reason="Synthetic PD-2b review.",
    )

    after = _verification(scratch_engine, document_id)
    verified = {
        "doc_no",
        "doc_date",
        "vendor_name",
        "grand_total",
        "po_refs",
        "line 1 quantity",
        "line 1 rate",
    }
    assert verified <= set(before)
    assert {name for name, state in after.items() if state == "user"} == verified
    assert {name: state for name, state in after.items() if name not in verified} == {
        name: state for name, state in before.items() if name not in verified
    }
    assert {"vendor_gstin", "currency", "line 1 amount", "line 1 description"} <= set(after)
    rows = _evidence(scratch_engine, document_id)
    assert all(rows[name][-1]["verified_by"] == str(actor.actor_id) for name in verified)
    assert _audit_after(scratch_engine, document_id)["verified_fields"] == sorted(verified)


def test_saving_without_marking_reviewed_verifies_only_what_was_changed(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _upload(scratch_engine, "pd2b-unreviewed", tmp_path)
    version, header, lines = _current(scratch_engine, document_id)
    before = _verification(scratch_engine, document_id)
    header_patch, lines_patch = _shown(
        header, lines, set(INVOICE_HEADER_COLUMNS), set(INVOICE_LINE_COLUMNS)
    )
    header_patch["po_refs"] = ["PO-PD2B-CHANGED-1"]

    ingestion.correct_invoice_document(
        actor,
        document_id,
        expected_version=version,
        header_patch=header_patch,
        lines_patch=lines_patch,
        verified=False,
        reason="Synthetic PD-2b correction.",
    )

    after = _verification(scratch_engine, document_id)
    assert after.pop("po_refs") == "user"
    before.pop("po_refs")
    assert after == before
    assert _audit_after(scratch_engine, document_id)["verified_fields"] == []


def test_a_changed_reference_marks_only_the_values_shown(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    # Before PD-2b the header rule read a stale name: after a changed reference, other
    # header values could be verified whether or not the person was shown them.
    actor, ingestion, document_id = _upload(scratch_engine, "pd2b-changed-ref", tmp_path)
    version, header, lines = _current(scratch_engine, document_id)
    header_patch, lines_patch = _shown(header, lines, {"doc_no", "po_refs"}, set())
    header_patch["po_refs"] = ["PO-PD2B-CHANGED-2"]

    ingestion.correct_invoice_document(
        actor,
        document_id,
        expected_version=version,
        header_patch=header_patch,
        lines_patch=lines_patch,
        verified=True,
        reason="Synthetic PD-2b review.",
    )

    after = _verification(scratch_engine, document_id)
    assert {name for name, state in after.items() if state == "user"} == {"doc_no", "po_refs"}
    assert _audit_after(scratch_engine, document_id)["verified_fields"] == ["doc_no"]


def test_a_value_read_by_ai_is_not_verified_by_marking_the_invoice_reviewed(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _upload(scratch_engine, "pd2b-ai", tmp_path)
    grand_total = _evidence(scratch_engine, document_id)["grand_total"][-1]
    _append(scratch_engine, grand_total, origin="ai", source="ai", confidence=1)
    version, header, lines = _current(scratch_engine, document_id)
    header_patch, lines_patch = _shown(
        header, lines, {"doc_no", "grand_total"}, {"quantity", "rate", "amount"}
    )

    ingestion.correct_invoice_document(
        actor,
        document_id,
        expected_version=version,
        header_patch=header_patch,
        lines_patch=lines_patch,
        verified=True,
        reason="Synthetic PD-2b review.",
    )

    rows = _evidence(scratch_engine, document_id)
    assert [(row["origin"], row["verification"]) for row in rows["grand_total"]] == [("ai", "none")]
    assert rows["doc_no"][-1]["verification"] == "user"
    assert "grand_total" not in _audit_after(scratch_engine, document_id)["verified_fields"]


def test_a_later_verification_survives_the_next_correction(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _upload(scratch_engine, "pd2b-latest", tmp_path)
    currency = _evidence(scratch_engine, document_id)["currency"][-1]
    _append(
        scratch_engine,
        currency,
        verification="user",
        verified_by=str(actor.actor_id),
        verified_at=sa.func.now(),
    )
    version, header, lines = _current(scratch_engine, document_id)
    header_patch, lines_patch = _shown(header, lines, {"doc_no"}, set())
    header_patch["doc_no"] = "INV-PD2B-1-CORRECTED"

    ingestion.correct_invoice_document(
        actor,
        document_id,
        expected_version=version,
        header_patch=header_patch,
        lines_patch=lines_patch,
        verified=False,
        reason="Synthetic PD-2b correction.",
    )

    rows = _evidence(scratch_engine, document_id)
    # One row per value moves to the new version: the latest one.
    assert all(len(items) == 1 for items in rows.values())
    assert rows["currency"][0]["verification"] == "user"
