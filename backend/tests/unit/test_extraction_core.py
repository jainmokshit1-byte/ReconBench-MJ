"""Layout extraction core on synthetic words (no real documents).

Covers OCR-tolerant readings, the cross-check solver (including the guards
that keep wrong values from ever being marked verified), tables, page
classification and the end-to-end document assembly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from reconbench_core.extraction.extract import PageKind, classify_page, extract_document
from reconbench_core.extraction.layout import (
    DocumentLayout,
    PageLayout,
    PageSource,
    Word,
    full_rows,
    layout_from_dict,
    layout_to_dict,
)
from reconbench_core.extraction.values import (
    amount_in_words,
    find_dates,
    read_gstin,
    read_number,
)
from reconbench_core.extraction.verify import (
    DocumentNumbers,
    LineNumbers,
    Num,
    Status,
    confusable,
    solve_document,
    solve_line,
)
from reconbench_core.schema import DocType

D = Decimal
VENDOR_GSTIN = "27AABCP6338G1ZD"
BUYER_GSTIN = "27AACCB3357N1ZE"

# --- values --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected", "repaired"),
    [
        ("1,24,250.00", "124250.00", False),
        ("2,979,420.0@", "2979420.00", True),
        ("5,000.00)", "5000.00", False),
        ("Rs.10,030.00", "10030.00", False),
        ("5,97,641.00°", "597641.00", False),
        (".35", "0.35", False),
        ("-₹38.10", "-38.10", False),
        ("20689,01", "20689.01", True),
    ],
)
def test_read_number(token: str, expected: str, repaired: bool) -> None:
    reading = read_number(token)
    assert reading is not None
    assert reading.value == D(expected)
    assert reading.repaired is repaired


@pytest.mark.parametrize("token", ["abc", "83@2.49.00", "Rs.", "1.2.3", "@@@@"])
def test_read_number_rejects_non_numbers(token: str) -> None:
    assert read_number(token) is None


def test_find_dates_handles_indian_formats_without_swallowing_neighbours() -> None:
    dates = [
        r.value for r in find_dates("Dated 5-Aug-26 and 2026-08-10 and 28-Aug-26 ; 31/08/2026")
    ]
    assert dates == [date(2026, 8, 5), date(2026, 8, 10), date(2026, 8, 28), date(2026, 8, 31)]


def test_gstin_repair_is_checksum_verified_and_unique() -> None:
    assert read_gstin(VENDOR_GSTIN).value == VENDOR_GSTIN
    repaired = read_gstin("GSTIN: 27AABCP6338GIZD")
    assert repaired is not None and repaired.value == VENDOR_GSTIN and repaired.repaired
    assert read_gstin("27AABCP6338G1ZX") is None


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("INR One Lakh Forty Six Thousand Six Hundred Fifteen Only", "146615.00"),
        ("Rupees Nine Thousand Eight Hundred Thirteen and Thirty Six Paise Only", "9813.36"),
        (
            "RUPEES EIGHT MILLION ONE HUNDRED FIFTY FOUR THOUSAND ONE HUNDRED FORTY AND TEN PAISA",
            "8154140.10",
        ),
        ("Two Hundred Eighty-two only", "282.00"),
    ],
)
def test_amount_in_words(text: str, value: str) -> None:
    assert amount_in_words(text) == D(value)


# --- solver --------------------------------------------------------------------------------


def test_confusable_accepts_known_ocr_slips_only() -> None:
    assert confusable(D("804698.78"), D("804698.70"))
    assert confusable(D("650.00"), D("27650.00"))
    assert not confusable(D("650.00"), D("27651.00"))
    assert not confusable(D("1234.00"), D("1299.00"))


def _line(q: str | None, r: str | None, a: str | None, **extra: object) -> LineNumbers:
    return LineNumbers(
        Num(D(q) if q else None), Num(D(r) if r else None), Num(D(a) if a else None), **extra
    )


def test_consistent_line_is_confirmed() -> None:
    line = solve_line(_line("2", "21500.00", "43000.00"))
    assert line.verified


def test_inconsistent_line_is_never_marked_verified() -> None:
    line = solve_line(_line("3", "21500.00", "43000.00"))
    assert not line.verified
    assert line.quantity.status == Status.UNVERIFIED


def test_rate_equal_to_amount_fixes_a_misread_quantity() -> None:
    line = _line("4", "5000.00", "5000.00")
    line.amount.status = Status.CONFIRMED
    solve_line(line)
    assert line.quantity.value == 1 and line.quantity.status == Status.CORRECTED
    assert line.rate.verified


def test_hidden_rate_digits_are_recovered_from_subtotal() -> None:
    line = _line(
        None, "813.00", "758284.40", subtotal=Num(D("757626.00")), freight=Num(D("658.40"))
    )
    solve_line(line)
    assert line.quantity.value == 2
    assert line.rate.value == D("378813.00") and line.rate.status == Status.CORRECTED
    assert line.amount.verified and line.freight.verified


def test_extended_minus_taxes_corrects_amount_digits() -> None:
    line = _line(
        None,
        None,
        "804698.78",
        extended=Num(D("949544.46")),
        tax_rate=D("9"),
        taxes=[Num(D("72422.88")), Num(D("72422.88"))],
    )
    solve_line(line)
    assert line.amount.value == D("804698.70") and line.amount.status == Status.CORRECTED


def test_quantity_from_division_alone_stays_derived() -> None:
    line = _line(None, "21500.00", "43000.00")
    line.amount.status = Status.CONFIRMED
    line.rate.status = Status.CONFIRMED
    solve_line(line)
    assert line.quantity.value == 2 and line.quantity.status == Status.DERIVED


def _doc(**candidates: list[str]) -> DocumentNumbers:
    def first(name: str) -> Num:
        values = candidates.get(name, [])
        return Num(D(values[0]) if values else None)

    return DocumentNumbers(
        taxable=first("taxable"),
        cgst=first("cgst"),
        sgst=first("sgst"),
        igst=first("igst"),
        grand_total=first("grand"),
        taxable_candidates=[D(v) for v in candidates.get("taxable", [])],
        grand_candidates=[D(v) for v in candidates.get("grand", [])],
        tax_candidates={k: [D(v) for v in candidates.get(k, [])] for k in ("cgst", "sgst", "igst")},
        words_totals=[D(v) for v in candidates.get("words", [])],
    )


def test_totals_search_picks_the_consistent_combination() -> None:
    lines = [_line("1", "1950.00", "1950.00"), _line("1", "6366.40", "6366.40")]
    doc = _doc(
        taxable=["8316.40"],
        cgst=["572.98", "748.48"],
        sgst=["572.98", "748.48"],
        grand=["1496.96", "9813.36"],
        words=["9813.36"],
    )
    solve_document(doc, lines)
    assert (doc.taxable.value, doc.cgst.value, doc.grand_total.value) == (
        D("8316.40"),
        D("748.48"),
        D("9813.36"),
    )
    assert doc.grand_total.verified and all(line.amount.verified for line in lines)


def test_unreadable_cgst_is_taken_from_sgst_only_when_totals_balance() -> None:
    doc = _doc(taxable=["6910288.24"], sgst=["621925.93"], grand=["8154140.10"])
    solve_document(doc, [])
    assert doc.cgst.value == D("621925.93") and doc.grand_total.verified


def test_amount_in_words_never_confirms_itself() -> None:
    doc = _doc(grand=["8055140.00"], words=["8155140.00"])
    solve_document(doc, [])
    assert not doc.grand_total.verified


def test_round_off_up_to_one_rupee_is_recorded() -> None:
    lines = [_line("1", "506475.00", "506475.00")]
    doc = _doc(taxable=["506475.00"], cgst=["45582.75"], sgst=["45582.75"], grand=["597641.00"])
    solve_document(doc, lines)
    assert doc.grand_total.verified and doc.round_off.value == D("0.50")


# --- layout, tables and documents ----------------------------------------------------------


def _row(y: int, *cells: tuple[int, str], conf: int = 96) -> list[Word]:
    words: list[Word] = []
    for x, text in cells:
        x0 = x
        for part in text.split(" "):
            words.append(Word(part, x0, y, x0 + len(part) * 14, y + 26, conf))
            x0 += len(part) * 14 + 12
    return words


def _page(number: int, rows: list[list[Word]], source: PageSource = PageSource.OCR) -> PageLayout:
    return PageLayout(number, 2480, 3505, tuple(w for row in rows for w in row), source)


def _invoice(
    *, doc_no: str = "SYN-INV-0042", amount2: str = "43,000.00", grand: str = "1,46,615.00"
) -> DocumentLayout:
    rows = [
        _row(100, (1000, "TAX INVOICE")),
        _row(200, (100, "Synthetic Traders Pvt Ltd"), (1500, "Invoice No."), (1900, "Dated")),
        _row(250, (1500, doc_no), (1900, "05/08/2026")),
        _row(300, (100, f"GSTIN/UIN: {VENDOR_GSTIN}")),
        _row(400, (100, "Buyer (Bill to)")),
        _row(450, (100, f"GSTIN/UIN {BUYER_GSTIN}")),
        _row(500, (100, "Consignee (Ship to)")),
        _row(550, (100, f"GSTIN/UIN {BUYER_GSTIN}")),
        _row(
            700,
            (100, "Sl"),
            (300, "Description of Goods"),
            (1100, "HSN/SAC"),
            (1400, "Quantity"),
            (1700, "Rate"),
            (2150, "Amount"),
        ),
        _row(
            780,
            (100, "1"),
            (300, "Processor Core 7"),
            (1100, "84733010"),
            (1400, "1 No"),
            (1700, "27,650.00"),
            (2150, "27,650.00"),
        ),
        _row(
            860,
            (100, "2"),
            (300, "DDR5 16GB RAM"),
            (1100, "84733099"),
            (1400, "2 No"),
            (1700, "21,500.00"),
            (2150, amount2),
        ),
        _row(
            940,
            (100, "3"),
            (300, "Graphics Card 8GB"),
            (1100, "84733030"),
            (1400, "1 No"),
            (1700, "53,600.00"),
            (2150, "53,600.00"),
        ),
        _row(1020, (700, "CGST Output"), (2150, "11,182.50")),
        _row(1100, (700, "SGST Output"), (2150, "11,182.50")),
        _row(1200, (700, "Total"), (1400, "4 No"), (2150, grand)),
        _row(1300, (100, "INR One Lakh Forty Six Thousand Six Hundred Fifteen Only")),
    ]
    return DocumentLayout((_page(1, rows),), 1)


def test_layout_round_trips_through_its_cache_format() -> None:
    layout = _invoice()
    assert layout_from_dict(layout_to_dict(layout)) == layout


def test_consistent_synthetic_invoice_is_fully_verified() -> None:
    extraction = extract_document(_invoice(), doc_type=DocType.INVOICE)
    assert extraction is not None
    document = extraction.document
    header = document.header
    assert header.doc_no.value == "SYN-INV-0042"
    assert header.doc_date.value == date(2026, 8, 5)
    assert header.vendor_gstin.value == VENDOR_GSTIN
    assert header.buyer_gstin.value == BUYER_GSTIN
    assert header.taxable_total.value == D("124250.00")
    assert header.grand_total.value == D("146615.00")
    assert [line.amount.value for line in document.lines] == [
        D("27650.00"),
        D("43000.00"),
        D("53600.00"),
    ]
    assert all(line.quantity.confidence >= D("0.98") for line in document.lines)
    assert not document.needs_verification


def test_inconsistent_numbers_send_the_invoice_to_review() -> None:
    extraction = extract_document(_invoice(amount2="44,000.00"), doc_type=DocType.INVOICE)
    assert extraction is not None
    document = extraction.document
    assert document.needs_verification
    assert "line 2" in document.header.extras["review_fields"]


def test_ambiguous_letter_digit_document_numbers_are_never_auto_verified() -> None:
    extraction = extract_document(_invoice(doc_no="PAPLO826/043"), doc_type=DocType.INVOICE)
    assert extraction is not None
    assert extraction.document.header.doc_no.confidence < D("0.70")
    assert "doc_no" in extraction.document.header.extras["review_fields"]


def test_eway_bill_pages_are_recognised_by_their_title() -> None:
    title = [Word("e-Way", 900, 120, 1100, 180), Word("Bill", 1130, 120, 1250, 180)]
    body = _row(400, (100, "Document Details: Tax Invoice - SYN-INV-0042 - 05/08/2026"))
    page = _page(2, [title, body])
    assert classify_page(page, full_rows(page)) == PageKind.EWAY_BILL
    invoice_page = _invoice().pages[0]
    assert classify_page(invoice_page, full_rows(invoice_page)) == PageKind.DOCUMENT


def test_eway_bill_document_number_confirms_the_invoice_number() -> None:
    title = [Word("e-Way", 900, 120, 1100, 180), Word("Bill", 1130, 120, 1250, 180)]
    body = _row(400, (100, "Document Details: Tax Invoice - SYN-INV-0042 - 05/08/2026"))
    layout = DocumentLayout((_invoice().pages[0], _page(2, [title, body])), 2)
    extraction = extract_document(layout, doc_type=DocType.INVOICE)
    assert extraction is not None
    assert extraction.checks["doc_no"].status == Status.CONFIRMED
    assert extraction.page_kinds[2] == PageKind.EWAY_BILL
