"""A split CGST/SGST rate, a freight column and unread header taxes are not false errors."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reconbench_core.recon import ReconciliationConfig, ReconciliationSnapshot, evaluate
from reconbench_core.recon.engine import _spread_freight_shares
from reconbench_core.schema import (
    CaseResult,
    CheckOutcome,
    DocType,
    Document,
    FieldValue,
    LineType,
    ReasonCode,
    Tolerance,
    ToleranceMode,
    ToleranceSource,
)
from tests.unit import test_phase4_reconciliation as recon_case


def _with(template: FieldValue[object], value: str) -> FieldValue[object]:
    return template.model_copy(update={"value": Decimal(value)})


def _invoice(
    *,
    line: dict[str, str] | None = None,
    header: dict[str, str | None] | None = None,
    extras: dict[str, str] | None = None,
) -> Document:
    """The one-line invoice (10 x 48000), with line/header values replaced as given."""

    document = recon_case._document(recon_case._invoice_xml("INV-2201", 10), DocType.INVOICE)
    template = document.lines[0].amount
    assert template is not None
    line_update: dict[str, object] = {
        name: _with(template, value) for name, value in (line or {}).items()
    }
    line_update["extras"] = extras or {}
    header_update: dict[str, object] = {
        name: None if value is None else _with(template, value)
        for name, value in (header or {}).items()
    }
    return document.model_copy(
        update={
            "header": document.header.model_copy(update=header_update),
            "lines": (document.lines[0].model_copy(update=line_update),),
        }
    )


def _po(tax_rate: str = "5") -> Document:
    po = recon_case._document(recon_case._po_xml().replace("20260101", "20260115"), DocType.PO)
    template = po.lines[0].amount
    assert template is not None
    return po.model_copy(
        update={"lines": (po.lines[0].model_copy(update={"tax_rate": _with(template, tax_rate)}),)}
    )


def _evaluate(invoice: Document) -> CaseResult:
    po = recon_case._record(1, _po(), DocType.PO)
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


# A 5% item billed as 2.5% CGST + 2.5% SGST: 480000 x 2.5% = 12000 each.
_SPLIT_HEADER: dict[str, str | None] = {
    "taxable_total": "480000",
    "cgst": "12000",
    "sgst": "12000",
    "igst": None,
    "grand_total": "504000",
}


def test_a_split_rate_on_a_five_percent_po_is_not_a_rate_difference() -> None:
    invoice = _invoice(
        line={"tax_rate": "2.5", "cgst_amount": "12000", "sgst_amount": "12000"},
        header=_SPLIT_HEADER,
    )

    result = _evaluate(invoice)

    assert ReasonCode.TAX_RATE_DIFF not in _codes(result)
    assert ReasonCode.MATH_ERROR not in _codes(result)
    assert CheckOutcome.FAIL not in _checks(result, "s6.computed_tax")
    assert _checks(result, "s6.computed_tax") == [CheckOutcome.PASS]


def test_a_genuine_two_and_a_half_percent_rate_still_differs_from_a_five_percent_po() -> None:
    invoice = _invoice(line={"tax_rate": "2.5"}, header=_SPLIT_HEADER)

    result = _evaluate(invoice)

    assert ReasonCode.TAX_RATE_DIFF in _codes(result)
    assert CheckOutcome.WARN in _checks(result, "s6.tax_rate_diff")


def test_a_freight_inclusive_line_amount_is_not_a_math_error() -> None:
    invoice = _invoice(
        line={"amount": "480294.38"},
        header={"taxable_total": "480294.38", "igst": None, "grand_total": "480294.38"},
        extras={"freight": "294.38"},
    )

    result = _evaluate(invoice)

    assert _checks(result, "s5.line_math") == [CheckOutcome.PASS]
    assert ReasonCode.MATH_ERROR not in _codes(result)


def test_a_freight_amount_with_comma_is_read() -> None:
    invoice = _invoice(
        line={"amount": "481294.38"},
        header={"taxable_total": "481294.38", "igst": None, "grand_total": "481294.38"},
        extras={"freight": "1,294.38"},
    )

    assert _checks(_evaluate(invoice), "s5.line_math") == [CheckOutcome.PASS]


def test_an_amount_that_matches_neither_figure_is_still_a_math_error() -> None:
    invoice = _invoice(
        line={"amount": "480500"},
        header={"taxable_total": "480500", "igst": None, "grand_total": "480500"},
        extras={"freight": "294.38"},
    )

    result = _evaluate(invoice)

    assert _checks(result, "s5.line_math") == [CheckOutcome.FAIL]
    assert ReasonCode.MATH_ERROR in _codes(result)


def test_an_unreadable_freight_value_is_ignored() -> None:
    invoice = _invoice(
        line={"amount": "480294.38"},
        header={"taxable_total": "480294.38", "igst": None, "grand_total": "480294.38"},
        extras={"freight": "n/a"},
    )

    assert _checks(_evaluate(invoice), "s5.line_math") == [CheckOutcome.FAIL]


def test_computed_tax_is_skipped_when_no_header_tax_was_read() -> None:
    invoice = _invoice(
        line={"tax_rate": "5"},
        header={"cgst": None, "sgst": None, "igst": None, "grand_total": "480000"},
    )

    assert _checks(_evaluate(invoice), "s6.computed_tax") == []


def test_a_header_tax_read_as_zero_is_still_checked() -> None:
    invoice = _invoice(
        line={"tax_rate": "5"},
        header={"cgst": None, "sgst": None, "igst": "0", "grand_total": "480000"},
    )

    result = _evaluate(invoice)

    assert _checks(result, "s6.computed_tax") == [CheckOutcome.FAIL]
    assert ReasonCode.MATH_ERROR in _codes(result)


# Freight printed once in the totals and also spread across the line amounts.
def _spread(
    amount: str, freight: str | None, *, second: tuple[str, str, str] | None = None
) -> Document:
    total = str(Decimal(amount) + (Decimal(second[2]) if second else 0))
    invoice = _invoice(
        line={"amount": amount},
        header={"taxable_total": total, "igst": None, "grand_total": total},
    )
    update: dict[str, object] = {}
    if freight is not None:
        update["extras"] = {"freight": freight}
    lines = invoice.lines
    if second is not None:
        qty, rate, line_amount = second
        base = invoice.lines[0]
        lines = (
            *lines,
            base.model_copy(
                update={
                    "line_no": 2,
                    "quantity": _with(base.quantity, qty),  # type: ignore[arg-type]
                    "rate": _with(base.rate, rate),  # type: ignore[arg-type]
                    "amount": _with(base.amount, line_amount),  # type: ignore[arg-type]
                }
            ),
        )
    return invoice.model_copy(
        update={"header": invoice.header.model_copy(update=update), "lines": lines}
    )


def _line_math(result: CaseResult) -> list[CheckOutcome]:
    return [c.result for c in result.checks if c.check_id == "s5.line_math"]


def test_freight_spread_into_a_line_that_equals_the_printed_freight_is_accepted() -> None:
    result = _evaluate(_spread("480294.38", "294.38"))

    assert _line_math(result) == [CheckOutcome.PASS]
    assert ReasonCode.MATH_ERROR not in _codes(result)
    assert ReasonCode.RATE_HIGH not in _codes(result)
    shown = next(c for c in result.checks if c.check_id == "s5.line_math")
    assert shown.evidence["freight_share"] == "294.38"
    assert shown.evidence["freight_total"] == "294.38"


def test_freight_spread_over_two_lines_is_accepted_when_the_shares_sum_to_the_print() -> None:
    result = _evaluate(_spread("480294.38", "299.38", second=("10", "100", "1005")))

    assert _line_math(result) == [CheckOutcome.PASS, CheckOutcome.PASS]
    assert ReasonCode.MATH_ERROR not in _codes(result)


def test_spread_freight_not_equal_to_the_printed_figure_stays_a_math_error() -> None:
    result = _evaluate(_spread("480294.38", "500"))

    assert _line_math(result) == [CheckOutcome.FAIL]
    assert ReasonCode.MATH_ERROR in _codes(result)


def test_a_negative_excess_is_never_freight() -> None:
    result = _evaluate(_spread("479705.62", "294.38"))

    assert _line_math(result) == [CheckOutcome.FAIL]


def test_one_negative_excess_blocks_the_other_lines_too() -> None:
    result = _evaluate(_spread("480294.38", "294.38", second=("10", "100", "990")))

    assert CheckOutcome.FAIL in _line_math(result)


def test_an_excess_larger_than_a_freight_share_is_not_accepted() -> None:
    result = _evaluate(_spread("520000", "40000"))

    assert _line_math(result) == [CheckOutcome.FAIL]


def test_an_excess_with_no_printed_freight_is_unchanged() -> None:
    result = _evaluate(_spread("480294.38", None))

    assert _line_math(result) == [CheckOutcome.FAIL]
    assert ReasonCode.MATH_ERROR in _codes(result)


def test_a_printed_freight_charge_line_counts_as_the_printed_figure() -> None:
    document = _spread("480294.38", None)
    base = document.lines[0]
    assert base.line_type is not None and base.description is not None
    charge = base.model_copy(
        update={
            "line_no": 2,
            "line_type": base.line_type.model_copy(update={"value": LineType.CHARGE}),
            "description": base.description.model_copy(update={"value": "Freight charges"}),
            "amount": _with(base.amount, "294.38"),  # type: ignore[arg-type]
            "quantity": None,
            "rate": None,
        }
    )
    document = document.model_copy(update={"lines": (base, charge)})
    tolerance = Tolerance(
        pct=Decimal("0"),
        abs=Decimal("0.05"),
        mode=ToleranceMode.OR,
        source=ToleranceSource.GLOBAL,
    )

    shares, printed = _spread_freight_shares(document, tolerance)

    assert shares == {1: Decimal("294.38")}
    assert printed == Decimal("294.38")
