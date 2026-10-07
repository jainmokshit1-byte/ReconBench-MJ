"""AC-1 deterministic guards on synthetic data: parties, GST structure, labels, XML items."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from reconbench_core.extraction.extract import accept_doc_no, accept_po, extract_document
from reconbench_core.extraction.layout import DocumentLayout, PageLayout, PageSource, Word
from reconbench_core.extraction.values import amount_in_words, find_dates
from reconbench_core.extraction.verify import (
    DocumentNumbers,
    LineNumbers,
    Num,
    Status,
    solve_document,
)
from reconbench_core.parsers import parse
from reconbench_core.parsers.parties import (
    guard_document,
    is_buyer_name,
    name_problem,
    party_hints_from_config,
)
from reconbench_core.parsers.types import BuyerEntityHint, ParserHints
from reconbench_core.schema import (
    DocLine,
    DocType,
    Document,
    FieldEvidence,
    FieldSource,
    FieldValue,
    InvoiceHeader,
)

D = Decimal
BUYER = "27AAPFU0939F1ZV"
LOCAL_SUPPLIER = "27AABCU9603R1ZN"
OTHER_STATE_SUPPLIER = "29AABCU9603R1ZJ"
ENTITIES = (BuyerEntityHint(gstin=BUYER, name="Synthetic Buyer Solutions Limited"),)
HINTS = ParserHints(doc_type=DocType.INVOICE, buyer_entities=ENTITIES)


def _field(value: object) -> FieldValue[object]:
    return FieldValue(
        value=value, confidence=D("0.98"), source=FieldSource.RULE, evidence=FieldEvidence()
    )


def _invoice(**fields: object) -> Document:
    header = InvoiceHeader(**{name: _field(value) for name, value in fields.items()})
    line = DocLine(line_no=1, quantity=_field(D("1")), amount=_field(D("1000.00")))
    return Document(header=header, lines=(line,))


# --- parties --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("(Bill from)", "label"),
        ("Supplier (Bill from)", "label"),
        ("M/s", "label"),
        (f"{LOCAL_SUPPLIER} - SYNTHETIC TRADERS", "gstin"),
        ("UP TO TWO UNITS SGST 9.00%", "line_text"),
        ("Date: 13-Jul-26", "line_text"),
        ("before the said effective date was", "line_text"),
        ("Synthetic Traders Pvt Ltd", None),
    ],
)
def test_name_problem_rejects_labels_numbers_and_line_text(name: str, problem: str | None) -> None:
    assert name_problem(name) == problem


def test_buyer_name_matches_spacing_suffix_and_dropped_words() -> None:
    assert is_buyer_name("SYNTHETIC BUYER SOLUTIONS LTD.", ENTITIES)
    assert is_buyer_name("Synthetic Buyer Solutions Ltd", ENTITIES)
    assert is_buyer_name("Synthetic Solutions Limited", ENTITIES)
    assert not is_buyer_name("Synthetic Traders Pvt Ltd", ENTITIES)


def test_buyer_gstin_is_moved_off_the_supplier_and_the_document_is_flagged() -> None:
    document = _invoice(vendor_gstin=BUYER, vendor_name="(Bill from)", doc_no="INV-1")

    guarded, warnings = guard_document(document, HINTS)

    assert guarded.header.vendor_gstin is None
    assert guarded.header.buyer_gstin is not None and guarded.header.buyer_gstin.value == BUYER
    assert guarded.header.vendor_name is None
    assert guarded.needs_verification
    assert {"vendor_gstin", "vendor_name"} <= set(guarded.header.extras["review_fields"])
    assert {w.code for w in warnings} == {"VENDOR_GSTIN_IS_BUYER_ENTITY", "VENDOR_NAME_REJECTED"}


def test_igst_on_an_intra_state_invoice_is_flagged() -> None:
    document = _invoice(
        vendor_gstin=LOCAL_SUPPLIER,
        buyer_gstin=BUYER,
        taxable_total=D("1000.00"),
        igst=D("180.00"),
        grand_total=D("1180.00"),
    )

    guarded, warnings = guard_document(document, HINTS)

    assert guarded.needs_verification
    assert "GST_TYPE_MISMATCH" in {w.code for w in warnings}


def test_consistent_gst_structure_passes_untouched() -> None:
    document = _invoice(
        vendor_gstin=OTHER_STATE_SUPPLIER,
        buyer_gstin=BUYER,
        taxable_total=D("1000.00"),
        igst=D("180.00"),
        grand_total=D("1180.00"),
    )

    guarded, warnings = guard_document(document, HINTS)

    assert not guarded.needs_verification
    assert warnings == []


def test_tax_outside_the_configured_slabs_is_flagged() -> None:
    document = _invoice(
        vendor_gstin=LOCAL_SUPPLIER,
        taxable_total=D("1000.00"),
        cgst=D("300.00"),
        sgst=D("300.00"),
        grand_total=D("1600.00"),
    )

    _guarded, warnings = guard_document(document, HINTS)

    assert "GST_RATE_OUT_OF_RANGE" in {w.code for w in warnings}


def test_party_hints_come_from_config() -> None:
    hints = party_hints_from_config(
        {
            "buyer_entities": [{"gstin": BUYER, "name": "Synthetic Buyer Solutions Limited"}],
            "gst_slabs": [{"rate": "18"}, {"rate": "5"}],
        }
    )
    assert hints["buyer_entities"] == ENTITIES
    assert hints["gst_rates"] == (D("5"), D("18"))


# --- values and solver ----------------------------------------------------------------------


def test_amount_in_words_ignores_trailing_noise_but_not_noise_between_numbers() -> None:
    assert amount_in_words("Rs. Five Thousand Two Hundred Forty Nine E & O E Only") == D("5249.00")
    assert amount_in_words("Rupees Sixty One Tines Hundred Only Thousand") is None


def test_month_name_with_a_lost_letter_is_read_as_repaired() -> None:
    (reading,) = find_dates("12- Au -26")
    assert reading.value == date(2026, 8, 12) and reading.repaired


def test_po_and_document_number_shapes() -> None:
    assert accept_po("501") is None
    assert accept_po("407-1234567-7654321") is None
    assert accept_po("PO/26-27/08-001_") == "PO/26-27/08-001"
    assert accept_doc_no("SYNPUR/26-") is None
    assert accept_doc_no("SYNPUR/26- 27/08-42") == "SYNPUR/26-27/08-42"


def _numbers(**values: list[str]) -> DocumentNumbers:
    def first(name: str) -> Num:
        return Num(D(values[name][0]) if values.get(name) else None)

    return DocumentNumbers(
        taxable=first("taxable"),
        cgst=first("cgst"),
        sgst=first("sgst"),
        igst=first("igst"),
        grand_total=first("grand"),
        taxable_candidates=[D(v) for v in values.get("taxable", [])],
        grand_candidates=[D(v) for v in values.get("grand", [])],
        tax_candidates={k: [D(v) for v in values.get(k, [])] for k in ("cgst", "sgst", "igst")},
        words_totals=[D(v) for v in values.get("words", [])],
    )


def test_interstate_documents_prefer_igst() -> None:
    def solved(interstate: bool | None) -> DocumentNumbers:
        doc = _numbers(
            taxable=["1000.00"], cgst=["90.00"], sgst=["90.00"], igst=["180.00"], grand=["1180.00"]
        )
        doc.interstate = interstate
        solve_document(doc, [_line_of("1000.00")])
        return doc

    across = solved(True)
    assert across.igst.value == D("180.00") and across.igst.verified
    assert across.cgst.value is None and across.sgst.value is None
    within = solved(False)
    assert within.cgst.value == D("90.00") and within.igst.value is None


def _line_of(amount: str) -> LineNumbers:
    return LineNumbers(quantity=Num(D("1")), rate=Num(D(amount)), amount=Num(D(amount)))


def test_rupee_sign_read_as_a_digit_is_corrected_only_with_the_words() -> None:
    doc = _numbers(grand=["1282.00"], words=["282.00"])
    doc.rupee_glyph = True
    solve_document(doc, [])
    assert doc.grand_total.value == D("282.00")
    assert doc.grand_total.status == Status.CORRECTED

    plain = _numbers(grand=["1282.00"], words=["282.00"])
    solve_document(plain, [])
    assert plain.grand_total.value == D("1282.00") and not plain.grand_total.verified


# --- layout: a Tally-style receipt note -----------------------------------------------------


def _row(y: int, *cells: tuple[int, str]) -> list[Word]:
    words: list[Word] = []
    for x, text in cells:
        x0 = x
        for part in text.split(" "):
            words.append(Word(part, x0, y, x0 + len(part) * 14, y + 26, 96))
            x0 += len(part) * 14 + 12
    return words


def _receipt_note() -> DocumentLayout:
    rows = [
        _row(80, (1100, "Receipt Note")),
        _row(
            150,
            (150, "Synthetic Buyer Solutions Limited"),
            (1500, "Receipt Note No."),
            (1900, "Dated"),
        ),
        _row(190, (150, "1, Synthetic Road"), (1500, "GRN/SYN/25-26/08-001"), (1900, "28-Aug-26")),
        _row(230, (1500, "Reference No. & Date."), (1900, "Other References")),
        _row(270, (1500, "PO/26-27/08-001 dt. 27-Aug-26")),
        _row(310, (150, f"GSTIN/UIN: {BUYER}")),
        _row(700, (150, "Supplier (Bill from)")),
        _row(745, (150, "SYNTHETIC TRADERS PRIVATE LIMITED")),
        _row(790, (150, f"GSTIN/UIN : {LOCAL_SUPPLIER}")),
        _row(1500, (150, "for Synthetic Buyer Solutions Limited")),
    ]
    page = PageLayout(1, 2480, 3505, tuple(w for row in rows for w in row), PageSource.OCR)
    return DocumentLayout((page,), 1)


def test_receipt_note_labels_parties_and_purchase_order_reference() -> None:
    extraction = extract_document(_receipt_note(), doc_type=DocType.GRN, buyer_entities=ENTITIES)

    assert extraction is not None
    header = extraction.document.header
    assert header.doc_no is not None and header.doc_no.value == "GRN/SYN/25-26/08-001"
    assert header.doc_date is not None and header.doc_date.value == date(2026, 8, 28)
    assert header.po_refs is not None and header.po_refs.value == ("PO/26-27/08-001",)
    assert header.vendor_gstin is not None and header.vendor_gstin.value == LOCAL_SUPPLIER
    assert header.buyer_gstin is not None and header.buyer_gstin.value == BUYER
    assert header.vendor_name is not None
    assert header.vendor_name.value == "SYNTHETIC TRADERS PRIVATE LIMITED"


def test_buyer_only_gstin_is_never_the_vendor() -> None:
    layout = _receipt_note()
    page = layout.pages[0]
    words = tuple(w for w in page.words if LOCAL_SUPPLIER not in w.text)
    layout = DocumentLayout((PageLayout(1, 2480, 3505, words, PageSource.OCR),), 1)

    extraction = extract_document(layout, doc_type=DocType.GRN, buyer_entities=ENTITIES)

    assert extraction is not None
    assert extraction.document.header.vendor_gstin is None
    assert extraction.document.header.buyer_gstin is not None


# --- Tally DBC XML --------------------------------------------------------------------------


_DBC = b"""<ENVELOPE>
  <DBCFIXED><DBCDATE>3-Apr-26</DBCDATE><DBCPARTY>Synthetic Traders Pvt Ltd</DBCPARTY></DBCFIXED>
  <DBCBUYERNAME>Synthetic Traders Pvt Ltd</DBCBUYERNAME><DBCVCHNO>GRN/SYN/04-002</DBCVCHNO>
  <DBCGSTIN>27AABCU9603R1ZN</DBCGSTIN><DBCQTY>1.00 PCS</DBCQTY>
  <DBCAMOUNT>-2200.00</DBCAMOUNT><DBCGROSSAMT>2596.00</DBCGROSSAMT>
  <DBCLEDAMT>-2200.00</DBCLEDAMT><DBCLEDAMT>-198.00</DBCLEDAMT><DBCLEDAMT>-198.00</DBCLEDAMT>
  <DBCFIXED><DBCDATE/><DBCPARTY>CABINET</DBCPARTY></DBCFIXED>
  <DBCVCHNO/><DBCQTY>1.00 PCS</DBCQTY><DBCRATE>2200.00/PCS</DBCRATE><DBCAMOUNT>-2200.00</DBCAMOUNT>
  <DBCFIXED><DBCDATE/><DBCPARTY>SYN-310 CABINET</DBCPARTY></DBCFIXED><DBCVCHNO/>
  <DBCFIXED><DBCDATE/><DBCPARTY>SYN-310-K3</DBCPARTY></DBCFIXED><DBCVCHNO/>
  <DBCFIXED><DBCDATE/><DBCPARTY>SN: SYN12345678,SYN87654321</DBCPARTY></DBCFIXED><DBCVCHNO/>
</ENVELOPE>"""


def test_dbc_item_name_comes_from_narration_with_serials_and_no_review() -> None:
    result = parse(_DBC, {"file_format": "xml", "doc_type": "grn"})

    assert result.success
    (document,) = result.documents
    (line,) = document.lines
    assert line.description is not None and line.description.value == "SYN-310 CABINET"
    assert line.item_code is not None and line.item_code.value == "SYN-310-K3"
    assert line.extras["serials"] == ["SYN12345678", "SYN87654321"]
    assert line.extras["dbc_stock_item"] == "CABINET"
    assert not document.needs_verification


def test_dbc_totals_that_do_not_reconcile_send_the_voucher_to_review() -> None:
    source = _DBC.replace(b"<DBCGROSSAMT>2596.00", b"<DBCGROSSAMT>2600.00")

    result = parse(source, {"file_format": "xml", "doc_type": "grn"})

    (document,) = result.documents
    assert document.needs_verification
    assert any(w.code == "DBC_TOTALS_UNRECONCILED" for w in result.warnings)
