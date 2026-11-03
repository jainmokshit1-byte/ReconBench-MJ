"""Disposable-database tests: a correction that fails arithmetic QA is not auto-verified.

The values are saved, the fields the QA failure names are not marked verified by a person
and stay under the 0.70 extraction gate; a person who checked them anyway saves again with
an override reason, which is audited. Every invoice here is synthetic.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.engine import Engine
from tests.integration.test_pd2b_review_marks_fields import (
    _audit_after,
    _current,
    _evidence,
    _shown,
    _upload,
    _verification,
)
from tests.integration.test_phase4_task_411_413 import scratch_engine

__all__ = ["scratch_engine"]

_GATE = Decimal("0.70")


def _save(
    engine: Engine,
    ingestion: Any,
    actor: Any,
    document_id: Any,
    *,
    taxable_total: Decimal | None,
    verified: bool = True,
    override_reason: str | None = None,
) -> None:
    version, header, lines = _current(engine, document_id)
    header_patch, lines_patch = _shown(
        header,
        lines,
        {"doc_no", "doc_date", "vendor_name", "grand_total", "po_refs"},
        {"quantity", "rate", "amount"},
    )
    if taxable_total is not None:
        header_patch["taxable_total"] = taxable_total
    ingestion.correct_invoice_document(
        actor,
        document_id,
        expected_version=version,
        header_patch=header_patch,
        lines_patch=lines_patch,
        verified=verified,
        reason="Synthetic QA correction.",
        override_reason=override_reason,
    )


def _held(engine: Engine, document_id: Any, name: str) -> dict[str, Any]:
    row = _evidence(engine, document_id)[name][-1]
    return row


def test_a_correction_that_fails_qa_is_saved_but_not_verified(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _upload(scratch_engine, "qa-fail", tmp_path)
    # The lines add up to 200; a taxable total taken from another document does not.
    _save(scratch_engine, ingestion, actor, document_id, taxable_total=Decimal("9999.00"))

    _version, header, _lines = _current(scratch_engine, document_id)
    assert header["taxable_total"] == Decimal("9999.00")
    assert header["extras"]["correction_qa"]["passed"] is False
    assert header["needs_verification"] is True
    for name in ("taxable_total", "grand_total"):
        row = _held(scratch_engine, document_id, name)
        assert row["verification"] != "user"
        assert row["source"] != "manual"
        assert row["confidence"] is not None and row["confidence"] < _GATE
    # Values the failure does not name are still verified as before.
    after = _verification(scratch_engine, document_id)
    assert after["doc_no"] == "user"
    audit = _audit_after(scratch_engine, document_id)
    assert {"taxable_total", "grand_total"} <= set(audit["qa_unverified_fields"])
    assert "grand_total" not in audit["verified_fields"]
    assert audit["qa_override"] is None


def test_a_correction_that_passes_qa_is_verified_as_before(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _upload(scratch_engine, "qa-pass", tmp_path)
    _version, header, _lines = _current(scratch_engine, document_id)
    _save(scratch_engine, ingestion, actor, document_id, taxable_total=header["taxable_total"])

    after = _verification(scratch_engine, document_id)
    assert after["grand_total"] == "user"
    assert after["doc_no"] == "user"
    audit = _audit_after(scratch_engine, document_id)
    assert audit["qa_unverified_fields"] == []
    assert audit["correction_qa"]["passed"] is True


def test_a_line_amount_failure_holds_that_lines_values(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _upload(scratch_engine, "qa-line", tmp_path)
    version, header, lines = _current(scratch_engine, document_id)
    header_patch, lines_patch = _shown(
        header, lines, {"doc_no", "grand_total"}, {"quantity", "rate", "amount"}
    )
    lines_patch[0]["amount"] = Decimal("5000.00")
    ingestion.correct_invoice_document(
        actor,
        document_id,
        expected_version=version,
        header_patch=header_patch,
        lines_patch=lines_patch,
        verified=True,
        reason="Synthetic QA correction.",
    )

    after = _verification(scratch_engine, document_id)
    for name in ("line 1 quantity", "line 1 rate", "line 1 amount"):
        assert after[name] != "user"
    row = _held(scratch_engine, document_id, "line 1 amount")
    assert row["confidence"] < _GATE and row["source"] != "manual"
    assert after["doc_no"] == "user"


def test_an_explicit_override_verifies_the_held_values_and_is_audited(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _upload(scratch_engine, "qa-override", tmp_path)
    _save(scratch_engine, ingestion, actor, document_id, taxable_total=Decimal("9999.00"))
    assert _verification(scratch_engine, document_id)["grand_total"] != "user"

    # Saving again unchanged, with a reason, is the person's deliberate verification.
    _save(
        scratch_engine,
        ingestion,
        actor,
        document_id,
        taxable_total=None,
        override_reason="  Checked against the printed invoice.  ",
    )

    after = _verification(scratch_engine, document_id)
    assert after["taxable_total"] == "user"
    assert after["grand_total"] == "user"
    _version, header, _lines = _current(scratch_engine, document_id)
    assert header["needs_verification"] is False
    audit = _audit_after(scratch_engine, document_id)
    assert audit["qa_override"]["reason"] == "Checked against the printed invoice."
    assert "taxable total" in audit["qa_override"]["failures"]
    assert audit["qa_unverified_fields"] == []


def test_an_override_reason_does_nothing_when_qa_passes(
    scratch_engine: Engine, tmp_path: Any
) -> None:
    actor, ingestion, document_id = _upload(scratch_engine, "qa-noop", tmp_path)
    _version, header, _lines = _current(scratch_engine, document_id)
    _save(
        scratch_engine,
        ingestion,
        actor,
        document_id,
        taxable_total=header["taxable_total"],
        override_reason="Not needed.",
    )

    assert _audit_after(scratch_engine, document_id)["qa_override"] is None
    assert _verification(scratch_engine, document_id)["grand_total"] == "user"
