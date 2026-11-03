"""Read again with rules on a disposable PostgreSQL 16 database, no gate patched.

A person can make the rules read the stored file of a ready document once more. The
result is a new current version (older versions, a person's save included, stay); it is a
fresh reading, so nothing a person verified is carried over; it never calls an AI provider,
even with the scanned-invoice switch on AI; it is audited; and when the file no longer
gives this document, nothing changes. Synthetic documents only.
"""

from __future__ import annotations

import io
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from reconbench_app import ingestion as ingestion_module
from reconbench_app.db.metadata import metadata
from reportlab.pdfgen import canvas
from scripts.vision_fixtures import SUPPLIER_GSTIN, SUPPLIER_NAME, synthetic_invoice_pdf
from sqlalchemy.engine import Engine
from tests.integration.test_ai_vision import (
    CSRF,
    World,
    engine,
    keys,
    make_world,
)
from tests.integration.test_pd2c_pd2d_reading import (
    SETTINGS,
    _company_server,
    _current,
    _request_count,
    _upload,
)
from tests.integration.test_pd2f_waiting_for_reading import _audit, _etag, _reading
from tests.support.scan_images import scanned_invoice_pdf, tesseract_with_english

__all__ = ["engine", "keys", "make_world"]

DOCUMENTS = metadata.tables["document"]
VERSIONS = metadata.tables["document_version"]
EVIDENCE = metadata.tables["field_evidence"]
DIRTY = metadata.tables["dirty_scope"]


def _other_invoice_pdf() -> bytes:
    """A second, different digital invoice."""

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(595, 842))
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(200, 790, "TAX INVOICE")
    pdf.setFont("Helvetica", 10)
    for index, line in enumerate(
        (
            "Invoice No: SYN/OTHER/26-27/0042",
            "Invoice Date: 03/04/2026",
            f"Supplier: {SUPPLIER_NAME}",
            f"Supplier GSTIN: {SUPPLIER_GSTIN}",
            "Buyer PO No: PO/SYN/26-27/04-077",
            "1  Synthetic Laptop Model X1   8471   2 Nos   50,000.00   1,00,000.00",
            "Taxable Value: 1,00,000.00",
            "CGST 9%: 9,000.00",
            "SGST 9%: 9,000.00",
            "Grand Total: 1,18,000.00",
        )
    ):
        pdf.drawString(40, 750 - index * 18, line)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _reread(world: World, document_id: UUID, *, etag: str | None = None) -> Any:
    return world.client.post(
        f"/api/v1/documents/{document_id}/reading/reread-rules",
        headers={"If-Match": etag or _etag(world, document_id), "X-CSRF-Token": CSRF},
    )


def _versions(engine: Engine, document_id: UUID) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.select(VERSIONS)
                .where(VERSIONS.c.document_id == document_id)
                .order_by(VERSIONS.c.seq)
            )
            .mappings()
            .all()
        ]


def _document_row(engine: Engine, document_id: UUID) -> dict[str, Any]:
    with engine.connect() as connection:
        return dict(
            connection.execute(sa.select(DOCUMENTS).where(DOCUMENTS.c.id == document_id))
            .mappings()
            .one()
        )


def _person_checked(engine: Engine, version_id: UUID) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(EVIDENCE)
                .where(
                    EVIDENCE.c.document_version_id == version_id,
                    sa.or_(EVIDENCE.c.origin == "manual", EVIDENCE.c.verification == "user"),
                )
            )
            or 0
        )


def _save_by_hand(world: World, document_id: UUID, doc_no: str) -> None:
    saved = world.client.put(
        f"/api/v1/documents/{document_id}/correction",
        json={"doc_no": doc_no},
        headers={"If-Match": _etag(world, document_id), "X-CSRF-Token": CSRF},
    )
    assert saved.status_code == 200, saved.text


def test_reread_makes_a_new_rules_version_and_keeps_the_older_ones(
    engine: Engine, make_world: Any
) -> None:
    """Even with the switch on AI: 0 provider requests, 0 vision jobs, an audit row, and a
    person's earlier save stays as an older version without being copied."""

    world = make_world()
    _company_server(engine, world, capable=True, scanned="ai")
    [document_id] = _upload(world, synthetic_invoice_pdf(), name="reread.pdf")
    [other_id] = _upload(world, _other_invoice_pdf(), name="other.pdf")
    other_before = _document_row(engine, other_id)
    first = _current(engine, document_id)
    _save_by_hand(world, document_id, "SYN-INV-HAND-1")
    manual = _current(engine, document_id)
    assert manual["extraction_method"] == "manual" and manual["doc_no"] == "SYN-INV-HAND-1"
    assert _person_checked(engine, manual["id"]) > 0

    response = _reread(world, document_id)

    assert response.status_code == 200, response.text
    fresh = _current(engine, document_id)
    assert fresh["id"] not in {first["id"], manual["id"]}
    assert fresh["extraction_method"] == "pdf_text" and fresh["reading_method"] == "rules"
    assert fresh["doc_no"] == first["doc_no"] != "SYN-INV-HAND-1"
    assert fresh["seq"] == manual["seq"] + 1
    assert [row["id"] for row in _versions(engine, document_id)] == [
        first["id"],
        manual["id"],
        fresh["id"],
    ]
    assert _person_checked(engine, fresh["id"]) == 0
    assert response.json()["version"]["id"] == str(fresh["id"])
    assert response.headers["etag"] == _etag(world, document_id)
    assert _reading(engine, document_id) == ("ready", None)

    [(before, after)] = _audit(engine, world, "document.reread_rules")
    assert before == {"document_version_id": str(manual["id"])}
    assert after == {"document_version_id": str(fresh["id"])}
    # No AI at all: the switch is on AI, yet nothing was queued or requested.
    assert _request_count(engine, world) == 0
    assert world.queue.vision_versions == [] and world.ai_queue.request_ids == []
    # Another document was not touched.
    assert _document_row(engine, other_id) == other_before
    # The follow-ups a normal parse queues: reconciliation for this invoice.
    with engine.connect() as connection:
        assert connection.scalar(
            sa.select(sa.func.count())
            .select_from(DIRTY)
            .where(DIRTY.c.scope_id == document_id, DIRTY.c.processed_at.is_(None))
        )


def test_a_second_reread_is_another_new_version(engine: Engine, make_world: Any) -> None:
    world = make_world(vision_on=False)
    [document_id] = _upload(world, synthetic_invoice_pdf(), name="twice.pdf")
    assert _reread(world, document_id).status_code == 200
    assert _reread(world, document_id).status_code == 200
    assert [row["seq"] for row in _versions(engine, document_id)] == [1, 2, 3]
    assert len(_audit(engine, world, "document.reread_rules")) == 2


def test_a_scanned_invoice_is_read_again_by_rules_when_the_switch_is_on_ai(
    engine: Engine, make_world: Any
) -> None:
    if not tesseract_with_english():
        pytest.skip("Tesseract with English data is required to read a synthetic scan")
    world = make_world()
    _company_server(engine, world, capable=True, scanned="rules")
    [document_id] = _upload(world, scanned_invoice_pdf(), name="scan-reread.pdf")
    first = _current(engine, document_id)
    with engine.begin() as connection:
        connection.execute(
            sa.update(SETTINGS)
            .where(SETTINGS.c.workspace_id == world.workspace_id)
            .values(scanned_invoice_reader="ai", version=SETTINGS.c.version + 1)
        )

    assert _reread(world, document_id).status_code == 200

    fresh = _current(engine, document_id)
    assert fresh["id"] != first["id"]
    assert (fresh["extraction_method"], fresh["reading_method"]) == ("pdf_ocr", "rules")
    assert _reading(engine, document_id) == ("ready", None)
    assert world.queue.vision_versions == [] and _request_count(engine, world) == 0


def test_reread_is_refused_while_the_document_waits_for_its_reading(
    engine: Engine, make_world: Any
) -> None:
    world = make_world(vision_on=False)
    [document_id] = _upload(world, synthetic_invoice_pdf(), name="waiting.pdf")
    etag = _etag(world, document_id)
    with engine.begin() as connection:
        connection.execute(
            sa.update(DOCUMENTS)
            .where(DOCUMENTS.c.id == document_id)
            .values(reading_state="waiting", version=DOCUMENTS.c.version + 1)
        )
    stale = _reread(world, document_id, etag=etag)
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"

    refused = _reread(world, document_id)

    assert refused.status_code == 422 and refused.json()["code"] == "reread_not_ready"
    assert len(_versions(engine, document_id)) == 1
    assert _audit(engine, world, "document.reread_rules") == []


def test_reread_of_an_unknown_document_is_not_found(make_world: Any) -> None:
    world = make_world(vision_on=False)
    response = world.client.post(
        "/api/v1/documents/00000000-0000-4000-8000-000000000001/reading/reread-rules",
        headers={"If-Match": '"1"', "X-CSRF-Token": CSRF},
    )
    assert response.status_code == 404


def test_reread_changes_nothing_when_the_file_gives_another_number_of_documents(
    engine: Engine, make_world: Any, monkeypatch: Any
) -> None:
    world = make_world(vision_on=False)
    [document_id] = _upload(world, synthetic_invoice_pdf(), name="count.pdf")
    before = _document_row(engine, document_id)
    real = ingestion_module.parse_pdf_isolated

    def two_documents(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        return result.model_copy(update={"documents": (*result.documents, *result.documents)})

    monkeypatch.setattr(ingestion_module, "parse_pdf_isolated", two_documents)

    response = _reread(world, document_id)

    assert response.status_code == 422
    assert response.json()["code"] == "reread_document_count_changed"
    assert _document_row(engine, document_id) == before
    assert len(_versions(engine, document_id)) == 1


def test_reread_changes_nothing_when_the_document_is_not_in_the_new_reading(
    engine: Engine, make_world: Any, monkeypatch: Any
) -> None:
    world = make_world(vision_on=False)
    [document_id] = _upload(world, synthetic_invoice_pdf(), name="gone.pdf")
    # The stored identity no longer matches what the rules read from the file now: another
    # natural key, and the document sits at another place in the file.
    with engine.begin() as connection:
        connection.execute(
            sa.update(DOCUMENTS).where(DOCUMENTS.c.id == document_id).values(natural_key="0" * 64)
        )
    real = ingestion_module.parse_pdf_isolated

    def moved(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        documents = tuple(
            document.model_copy(update={"source_locator": "layout:p9-9"})
            for document in result.documents
        )
        return result.model_copy(update={"documents": documents})

    monkeypatch.setattr(ingestion_module, "parse_pdf_isolated", moved)
    before = _document_row(engine, document_id)

    response = _reread(world, document_id)

    assert response.status_code == 422
    assert response.json()["code"] == "reread_document_unmatched"
    assert _document_row(engine, document_id) == before
    assert len(_versions(engine, document_id)) == 1
    assert _audit(engine, world, "document.reread_rules") == []
