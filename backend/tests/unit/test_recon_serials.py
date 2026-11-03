"""s4.serials: serial numbers on the invoice line and the GRN line for the same PO line."""

from __future__ import annotations

from datetime import date

from reconbench_core.recon import ReconciliationConfig, ReconciliationSnapshot, evaluate
from reconbench_core.schema import (
    CaseResult,
    CaseStatus,
    CheckOutcome,
    DocType,
    Document,
    ReasonCode,
)
from tests.unit import test_phase4_reconciliation as recon_case


def _with_serials(document: Document, serials: object) -> Document:
    if serials is None:
        return document
    lines = tuple(
        line.model_copy(update={"extras": {**line.extras, "serials": serials}})
        for line in document.lines
    )
    return document.model_copy(update={"lines": lines})


def _evaluate(invoice_serials: object = None, grn_serials: object = None) -> CaseResult:
    po_doc = recon_case._document(recon_case._po_xml().replace("20260101", "20260115"), DocType.PO)
    po = recon_case._record(1, po_doc, DocType.PO)
    grn_doc = _with_serials(
        recon_case._document(recon_case._grn_xml(10, accepted=10), DocType.GRN), grn_serials
    )
    grn = recon_case._record(2, grn_doc, DocType.GRN)
    invoice_doc = _with_serials(
        recon_case._document(recon_case._invoice_xml("INV-2201", 10), DocType.INVOICE),
        invoice_serials,
    )
    record = recon_case._record(3, invoice_doc, DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, record),
        links=(
            recon_case._link(10, grn.document_id, po.document_id),
            recon_case._link(11, record.document_id, po.document_id),
        ),
    )
    return evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]


def _serial_checks(result: CaseResult) -> list[object]:
    return [
        check for line in result.lines for check in line.checks if check.check_id == "s4.serials"
    ]


def test_baseline_without_serials_is_matched() -> None:
    result = _evaluate()

    assert result.status is CaseStatus.MATCHED
    assert not _serial_checks(result)


def test_equal_serials_raise_no_check() -> None:
    result = _evaluate(["SN1234567", "SN7654321"], ["sn-1234567", "SN 7654321"])

    assert result.status is CaseStatus.MATCHED
    assert not _serial_checks(result)
    assert ReasonCode.SERIAL_DIFF not in result.reason_codes


def test_one_character_difference_warns_and_matches_with_flags() -> None:
    result = _evaluate(["SN1234567", "SN7654321"], ["SN1234567", "SN7654322"])

    [check] = _serial_checks(result)
    assert check.result is CheckOutcome.WARN  # type: ignore[attr-defined]
    assert check.code is ReasonCode.SERIAL_DIFF  # type: ignore[attr-defined]
    evidence = check.evidence  # type: ignore[attr-defined]
    assert evidence["invoice_count"] == 2 and evidence["grn_count"] == 2
    assert evidence["only_on_invoice"] == ["SN7654321"]
    assert evidence["only_on_grn"] == ["SN7654322"]
    assert ReasonCode.SERIAL_DIFF in result.reason_codes
    assert result.status is CaseStatus.MATCHED_FLAGS
    assert result.status is not CaseStatus.NEEDS_REVIEW


def test_a_text_cell_of_serials_from_the_ai_reader_is_compared_too() -> None:
    result = _evaluate("SN1234567, SN7654321", ["SN1234567", "SN7654321"])

    assert not _serial_checks(result)
    assert _evaluate("SN1234567, SN7654399", ["SN1234567", "SN7654321"]).status is (
        CaseStatus.MATCHED_FLAGS
    )


def test_a_part_billed_line_with_a_subset_of_the_receipt_serials_is_not_flagged() -> None:
    result = _evaluate(["SN1234567"], ["SN1234567", "SN7654321"])

    assert not _serial_checks(result)


def test_serials_on_one_side_only_raise_no_check() -> None:
    assert not _serial_checks(_evaluate(["SN1234567"], None))
    assert not _serial_checks(_evaluate(None, ["SN1234567"]))
