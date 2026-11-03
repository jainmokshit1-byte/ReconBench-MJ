"""Header totals worked out from the lines are confirmed only by an independent printed total."""

from __future__ import annotations

from decimal import Decimal as D

from reconbench_core.extraction.verify import (
    DocumentNumbers,
    LineNumbers,
    Num,
    Status,
    solve_document,
)


def _line(amount: str, *taxes: str) -> LineNumbers:
    return LineNumbers(
        Num(D("1")),
        Num(D(amount)),
        Num(D(amount)),
        taxes=[Num(D(tax)) for tax in taxes],
    )


def _doc(
    *,
    taxable: str | None = None,
    grand: str | None = None,
    interstate: bool | None = None,
) -> DocumentNumbers:
    return DocumentNumbers(
        taxable=Num(D(taxable) if taxable else None),
        cgst=Num(None),
        sgst=Num(None),
        igst=Num(None),
        grand_total=Num(D(grand) if grand else None),
        grand_candidates=[D(grand)] if grand else [],
        interstate=interstate,
        gst_rates=(D("5"), D("12"), D("18"), D("28")),
    )


def test_unlabelled_tax_is_taken_from_the_lines_when_the_printed_total_agrees() -> None:
    doc = _doc(grand="354.00", interstate=True)

    solve_document(doc, [_line("100.00", "18.00"), _line("200.00", "36.00")])

    assert (doc.taxable.value, doc.igst.value, doc.grand_total.value) == (
        D("300.00"),
        D("54.00"),
        D("354.00"),
    )
    assert doc.taxable.verified and doc.igst.verified and doc.grand_total.verified


def test_split_line_taxes_give_equal_cgst_and_sgst() -> None:
    doc = _doc(grand="354.00", interstate=False)

    solve_document(doc, [_line("100.00", "9.00", "9.00"), _line("200.00", "18.00", "18.00")])

    assert (doc.cgst.value, doc.sgst.value) == (D("27.00"), D("27.00"))
    assert doc.igst.value is None
    assert doc.grand_total.verified


def test_line_taxes_that_do_not_add_up_to_the_printed_total_confirm_nothing() -> None:
    doc = _doc(grand="360.00", interstate=True)

    solve_document(doc, [_line("100.00", "18.00"), _line("200.00", "36.00")])

    assert not doc.grand_total.verified
    assert doc.igst.value is None


def test_a_missing_grand_total_is_derived_but_never_verified() -> None:
    doc = _doc(taxable="400.00")

    solve_document(doc, [_line("400.00")])

    assert doc.grand_total.value == D("400.00")
    assert doc.grand_total.status is Status.DERIVED
    assert not doc.grand_total.verified


def test_a_derived_grand_total_adds_the_taxes_read() -> None:
    doc = _doc(taxable="300.00", interstate=True)
    doc.igst = Num(D("54.00"))

    solve_document(doc, [_line("100.00"), _line("200.00")])

    assert doc.grand_total.value == D("354.00")
    assert doc.grand_total.status is Status.DERIVED


def test_nothing_is_derived_without_a_taxable_value() -> None:
    doc = _doc()

    solve_document(doc, [])

    assert doc.grand_total.value is None
