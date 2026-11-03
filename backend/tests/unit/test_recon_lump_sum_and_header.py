"""A lump-sum line is compared by amount (PD-3c); unread header totals are never Matched."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reconbench_core.recon import ReconciliationConfig, ReconciliationSnapshot, evaluate
from reconbench_core.schema import (
    CaseResult,
    CaseStatus,
    CheckOutcome,
    DocType,
    Document,
    FieldValue,
    ReasonCode,
)
from tests.unit import test_phase4_reconciliation as recon_case


def _with(template: FieldValue[object], value: str | None) -> FieldValue[object] | None:
    return None if value is None else template.model_copy(update={"value": Decimal(value)})


def _replace(
    document: Document, line: dict[str, str | None], header: dict[str, str | None]
) -> Document:
    template = document.lines[0].amount
    assert template is not None
    return document.model_copy(
        update={
            "header": document.header.model_copy(
                update={name: _with(template, value) for name, value in header.items()}
            ),
            "lines": (
                document.lines[0].model_copy(
                    update={name: _with(template, value) for name, value in line.items()}
                ),
            ),
        }
    )


_LUMP: dict[str, str | None] = {"quantity": None, "rate": None}


def _evaluate(
    *,
    invoice_line: dict[str, str | None],
    invoice_header: dict[str, str | None] | None = None,
    po_line: dict[str, str | None] | None = None,
) -> CaseResult:
    invoice = _replace(
        recon_case._document(recon_case._invoice_xml("INV-3301", 10), DocType.INVOICE),
        invoice_line,
        invoice_header or {},
    )
    po_doc = recon_case._document(recon_case._po_xml().replace("20260101", "20260115"), DocType.PO)
    po = recon_case._record(1, _replace(po_doc, po_line or {}, {}), DocType.PO)
    grn_doc = recon_case._document(recon_case._grn_xml(10, accepted=10), DocType.GRN)
    grn = recon_case._record(2, grn_doc, DocType.GRN)
    record = recon_case._record(3, invoice, DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(po, grn, record),
        links=(
            recon_case._link(10, grn.document_id, po.document_id),
            recon_case._link(11, record.document_id, po.document_id),
        ),
    )
    return evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 2))[0]


def _codes(result: CaseResult) -> set[ReasonCode]:
    return set(result.reason_codes) | {c for line in result.lines for c in line.reason_codes}


def _checks(result: CaseResult, check_id: str) -> list[CheckOutcome]:
    found = [c for c in result.checks if c.check_id == check_id]
    found += [c for line in result.lines for c in line.checks if c.check_id == check_id]
    return [c.result for c in found]


def _line_checks(result: CaseResult, check_id: str) -> list[CheckOutcome]:
    return [c.result for line in result.lines for c in line.checks if c.check_id == check_id]


def test_the_baseline_invoice_matches() -> None:
    result = _evaluate(invoice_line={})

    assert result.status is CaseStatus.MATCHED
    assert _line_checks(result, "s5.lump_sum") == []
    assert _checks(result, "s5.header_totals_read") == []


def test_a_lump_sum_line_equal_to_the_po_amount_is_matched_and_payable() -> None:
    result = _evaluate(invoice_line=_LUMP, po_line=_LUMP)

    assert _line_checks(result, "s5.lump_sum") == [CheckOutcome.PASS]
    assert result.status is CaseStatus.MATCHED
    assert result.payable_taxable == Decimal("480000")
    assert result.at_risk == Decimal("0")
    assert ReasonCode.TOTAL_MISMATCH not in _codes(result)


def test_a_lump_sum_line_above_the_po_amount_is_rate_high() -> None:
    result = _evaluate(
        invoice_line={**_LUMP, "amount": "490000"},
        invoice_header={"taxable_total": "490000", "grand_total": None},
        po_line=_LUMP,
    )

    assert _line_checks(result, "s5.lump_sum") == [CheckOutcome.FAIL]
    assert ReasonCode.RATE_HIGH in _codes(result)
    assert result.status is not CaseStatus.MATCHED
    assert result.payable_taxable == Decimal("480000")


def test_a_lump_sum_line_below_the_po_amount_is_rate_low_and_pays_the_invoice_amount() -> None:
    result = _evaluate(
        invoice_line={**_LUMP, "amount": "470000"},
        invoice_header={"taxable_total": "470000"},
        po_line=_LUMP,
    )

    assert _line_checks(result, "s5.lump_sum") == [CheckOutcome.WARN]
    assert ReasonCode.RATE_LOW in _codes(result)
    assert result.payable_taxable == Decimal("470000")


def test_a_lump_sum_invoice_line_against_an_itemised_po_line_needs_review() -> None:
    result = _evaluate(invoice_line=_LUMP)

    assert _line_checks(result, "s5.lump_sum") == [CheckOutcome.REVIEW]
    assert result.status is not CaseStatus.MATCHED
    assert result.payable_taxable == Decimal("0")


def test_a_lump_sum_line_with_no_po_amount_needs_review() -> None:
    result = _evaluate(invoice_line=_LUMP, po_line={**_LUMP, "amount": None})

    assert _line_checks(result, "s5.lump_sum") == [CheckOutcome.REVIEW]
    assert result.status is not CaseStatus.MATCHED


def test_a_read_grand_total_with_blank_taxable_is_never_matched() -> None:
    result = _evaluate(invoice_line={}, invoice_header={"taxable_total": None})

    assert _checks(result, "s5.header_totals_read") == [CheckOutcome.REVIEW]
    assert ReasonCode.LOW_CONFIDENCE_DATA in _codes(result)
    assert result.status is CaseStatus.NEEDS_REVIEW


def test_line_tax_with_no_header_tax_read_is_never_matched() -> None:
    result = _evaluate(
        invoice_line={"tax_rate": "18", "igst_amount": "86400"},
        invoice_header={"cgst": None, "sgst": None, "igst": None},
    )

    assert _checks(result, "s5.header_totals_read") == [CheckOutcome.REVIEW]
    assert result.status is CaseStatus.NEEDS_REVIEW
