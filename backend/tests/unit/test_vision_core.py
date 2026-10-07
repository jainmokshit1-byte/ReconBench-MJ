"""Vision reader checks (AC-5): normalisation, plan §7 hard rejects, document mapping.

All values are synthetic. Checksum-valid GSTINs are built with the real
checksum so the rules see them as genuine.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from reconbench_core.extraction.vision import (
    Box,
    SentPage,
    VisionAnnotation,
    VisionField,
    VisionLine,
    VisionReading,
    align_lines,
    check_reading,
    normalize_field,
    parse_money,
    parse_quantity,
    vision_document,
)
from reconbench_core.normalize.gstin import calculate_gstin_checksum
from reconbench_core.parsers.types import BuyerEntityHint
from reconbench_core.schema import DocType, Document, FieldSource


def _gstin(body: str) -> str:
    return body + calculate_gstin_checksum(body)


SUPPLIER = _gstin("27AAPFU0939F1Z")
BUYER = _gstin("27AABCS1234Q1Z")
OTHER_STATE_SUPPLIER = _gstin("29AAJCC9783E1Z")
BUYERS = (BuyerEntityHint(gstin=BUYER, name="Synthetic Buyer Private Limited"),)
PAGES = {1: SentPage(1, (Box(400, 50, 420, 300),))}
BOX = (100, 100, 120, 300)


def _field(name: str, text: str, *, page: int = 1, box: tuple[int, ...] = BOX) -> VisionField:
    return VisionField(name, text, page, box)


def _check(*fields: VisionField, lines: tuple[VisionLine, ...] = (), **kwargs: Any) -> Any:
    reading = VisionReading("invoice", fields=fields, lines=lines)
    return check_reading(reading, pages=PAGES, buyer_entities=BUYERS, **kwargs)


def _ocr(**header: str) -> Document:
    return Document.model_validate(
        {
            "header": {
                "doc_type": "invoice",
                **{
                    name: {
                        "value": value,
                        "confidence": "0.98",
                        "source": "rule",
                        "evidence": {},
                    }
                    for name, value in header.items()
                },
            }
        }
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("₹ 1,23,456.00", Decimal("123456.00")),
        ("Rs. 400/-", Decimal("400")),
        ("(-)0.31", Decimal("-0.31")),
        ("5,249.00", Decimal("5249.00")),
        ("18%", None),
        ("abc", None),
    ],
)
def test_money_is_parsed_from_the_printed_text(text: str, expected: Decimal | None) -> None:
    assert parse_money(text) == expected


def test_quantity_takes_the_leading_number() -> None:
    assert parse_quantity("2 Nos") == Decimal("2")
    assert parse_quantity("1.000 PCS") == Decimal("1.000")
    assert parse_quantity("PCS") is None


def test_fields_use_the_ocr_reader_acceptance_rules() -> None:
    assert normalize_field("doc_no", "SYN-INV-0042") == "SYN-INV-0042"
    assert normalize_field("date", "12-Aug-26") == date(2026, 8, 12)
    assert normalize_field("date", "28.08.2026") == date(2026, 8, 28)
    assert normalize_field("gstin", SUPPLIER.lower()) == SUPPLIER
    # Address digits and marketplace order numbers are never PO references (AC-1).
    assert normalize_field("po_ref", "501") is None
    assert normalize_field("po_ref", "407-0121734-2276374") is None
    assert normalize_field("po_ref", "PO/26-27/08-236") == "PO/26-27/08-236"


def test_box_must_be_inside_the_page_scale_and_ordered() -> None:
    assert Box.parse([10, 20, 30, 40]) == Box(10, 20, 30, 40)
    for bad in ([30, 20, 10, 40], [0, 0, 0, 1001], [1, 2, 3], [-1, 0, 5, 5]):
        assert Box.parse(bad) is None


def test_structural_checks_drop_unsent_pages_bad_boxes_and_masked_areas() -> None:
    check = _check(
        _field("doc_no", "A-1", page=2),
        _field("doc_date", "01/04/2026", box=(5, 5, 4, 9)),
        _field("supplier_name", "Hidden Name", box=(402, 60, 418, 290)),
        _field("grand_total", "1,000.00"),
    )
    assert [item.name for item in check.fields] == ["grand_total"]
    assert check.dropped == {"bad_page": 1, "bad_box": 1, "in_mask": 1}


def test_buyer_gstin_as_supplier_is_a_hard_reject() -> None:
    check = _check(_field("supplier_gstin", BUYER), _field("buyer_gstin", BUYER))
    assert [item.name for item in check.fields] == ["buyer_gstin"]
    assert check.dropped["supplier_is_buyer"] == 1


def test_gstin_with_a_wrong_checksum_is_rejected_not_repaired() -> None:
    wrong = SUPPLIER[:-1] + ("A" if SUPPLIER[-1] != "A" else "B")
    check = _check(_field("supplier_gstin", wrong))
    assert not check.fields and check.dropped["gstin_invalid"] == 1


def test_label_or_buyer_name_is_never_a_supplier_name() -> None:
    check = _check(
        _field("supplier_name", "Bill From"),
        _field("supplier_name", "SYNTHETIC BUYER PVT LTD"),
    )
    assert not check.fields and check.dropped["bad_name"] == 2


def test_tax_type_must_follow_the_state_rule() -> None:
    intra = _check(
        _field("supplier_gstin", SUPPLIER),
        _field("igst_total", "180.00"),
        _field("cgst_total", "90.00"),
    )
    assert {item.name for item in intra.fields} == {"supplier_gstin", "cgst_total"}
    inter = _check(
        _field("supplier_gstin", OTHER_STATE_SUPPLIER),
        _field("cgst_total", "90.00"),
        _field("igst_total", "180.00"),
    )
    assert {item.name for item in inter.fields} == {"supplier_gstin", "igst_total"}
    assert inter.dropped["gst_type"] == 1


def test_totals_breaking_arithmetic_that_ocr_satisfies_are_dropped() -> None:
    ocr = _ocr(taxable_total="1000.00", cgst="90.00", sgst="90.00", grand_total="1180.00")
    check = _check(
        _field("taxable_total", "1000.00"),
        _field("cgst_total", "90.00"),
        _field("sgst_total", "90.00"),
        _field("grand_total", "1190.00"),
        ocr=ocr,
    )
    assert {item.name for item in check.fields} == {"taxable_total", "cgst_total", "sgst_total"}
    assert check.dropped["arithmetic_conflict"] == 1


def test_consistent_vision_totals_are_kept_even_when_ocr_differs() -> None:
    ocr = _ocr(taxable_total="1000.00", cgst="90.00", sgst="90.00", grand_total="1180.00")
    check = _check(
        _field("taxable_total", "2000.00"),
        _field("cgst_total", "180.00"),
        _field("sgst_total", "180.00"),
        _field("grand_total", "2360.00"),
        ocr=ocr,
    )
    assert len(check.fields) == 4  # a real conflict for AC-6, not an arithmetic break


def test_single_valued_conflicts_drop_both_and_refs_may_repeat() -> None:
    check = _check(
        _field("doc_no", "A-100"),
        _field("doc_no", "A-101"),
        _field("po_ref", "PO/26-27/08-001"),
        _field("po_ref", "PO/26-27/08-002"),
    )
    assert [item.name for item in check.fields] == ["po_ref", "po_ref"]
    assert check.dropped["conflict"] == 2


def test_line_arithmetic_against_a_consistent_ocr_line() -> None:
    ocr = Document.model_validate(
        {
            "header": {"doc_type": "invoice"},
            "lines": [
                {
                    "line_no": 1,
                    **{
                        name: {
                            "value": value,
                            "confidence": "0.98",
                            "source": "rule",
                            "evidence": {},
                        }
                        for name, value in {
                            "quantity": "2",
                            "rate": "500.00",
                            "amount": "1000.00",
                        }.items()
                    },
                }
            ],
        }
    )
    line = VisionLine(
        1,
        (200, 50, 220, 900),
        {"description": "Item", "quantity": "3", "rate": "500.00", "amount": "1000.00"},
    )
    check = _check(lines=(line,), ocr=ocr)
    [accepted] = check.lines
    assert accepted.value("quantity") is None and accepted.value("amount") == Decimal("1000.00")
    assert check.dropped["line_arithmetic"] == 1


def test_annotations_stay_annotations() -> None:
    reading = VisionReading(
        "invoice",
        annotations=(VisionAnnotation("handwritten", "PO 26-27/04-777", "po_number", 1, BOX),),
    )
    check = check_reading(reading, pages=PAGES)
    document = vision_document(check, DocType.INVOICE)
    assert document.header.po_refs is None  # never a printed PO reference
    assert document.header.extras["annotation_refs"] == ["PO 26-27/04-777"]


def test_vision_document_is_unverified_ai_output() -> None:
    check = _check(
        _field("doc_no", "SYN-INV-0042"),
        _field("supplier_gstin", SUPPLIER),
        _field("grand_total", "1,180.00"),
        _field("po_ref", "PO/26-27/04-901"),
        _field("marketplace_order_ref", "407-0121734-2276374"),
        lines=(VisionLine(1, (200, 50, 220, 900), {"description": "Item", "amount": "1000.00"}),),
    )
    document = vision_document(check, DocType.INVOICE, buyer_entities=BUYERS)
    header = document.header
    assert document.needs_verification
    assert header.doc_no is not None and header.doc_no.source is FieldSource.AI
    assert header.doc_no.confidence < Decimal("0.70")
    assert header.vendor_gstin is not None and header.vendor_gstin.value == SUPPLIER
    assert header.po_refs is not None and header.po_refs.value == ("PO/26-27/04-901",)
    assert header.extras["marketplace_order_no"] == "407-0121734-2276374"
    assert document.lines[0].amount is not None
    assert document.lines[0].amount.value == Decimal("1000.00")


def test_grn_quantity_reads_as_received_quantity() -> None:
    check = _check(
        lines=(VisionLine(1, (200, 50, 220, 900), {"description": "UPS", "quantity": "1"}),)
    )
    document = vision_document(check, DocType.GRN)
    assert document.lines[0].received_qty is not None


def test_a_line_row_is_kept_for_a_description_an_amount_or_a_quantity() -> None:
    row = (200, 50, 220, 900)
    check = _check(
        lines=(
            VisionLine(1, row, {"description": "", "received_qty": "4"}),
            VisionLine(1, row, {"description": " ", "amount": "1000.00"}),
            VisionLine(1, row, {"description": "Cable"}),
            VisionLine(1, row, {"description": "", "line_no": "4", "uom": "Nos"}),
            VisionLine(1, row, {"description": "Mouse", "uom": "Nos 2 450.00 900.00 18%"}),
        )
    )
    assert len(check.lines) == 4
    assert check.dropped == {"empty_line": 1, "bad_cell": 1}  # the stuffed unit cell
    assert check.lines[3].value("uom") is None
    assert check.lines[0].value("received_qty") == Decimal("4")


def test_align_lines_prefers_amount_then_quantity_rate_then_description() -> None:
    lines = _check(
        lines=(
            VisionLine(1, (200, 50, 220, 900), {"description": "Mouse", "amount": "5000.00"}),
            VisionLine(
                1,
                (230, 50, 250, 900),
                {"description": "Laptop Model X1", "quantity": "2", "rate": "50000.00"},
            ),
            VisionLine(1, (260, 50, 280, 900), {"description": "Brand new cable"}),
        )
    ).lines
    current = [
        {
            "line_no": 1,
            "amount": "100000.00",
            "quantity": "2",
            "rate": "50000.00",
            "description": "Laptop Model X1",
        },
        {
            "line_no": 2,
            "amount": "5000.00",
            "quantity": "10",
            "rate": "500.00",
            "description": "Mouse",
        },
    ]
    assert align_lines(current, lines) == [2, 1, None]
