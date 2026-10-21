"""Per-stage miss attribution on synthetic truth and reader output (scan fix WI-1)."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from reconbench_core.parsers.types import PageText, ParseResult, ParseWarning
from reconbench_core.schema import (
    DocType,
    Document,
    FieldEvidence,
    FieldSource,
    FieldValue,
    FileFormat,
    InvoiceHeader,
)
from scripts.accuracy_attribution import Party, attribute, header_stage, scan_label
from scripts.accuracy_bench import Truth, TruthDocument, TruthFile
from tests.support.layouts import gstin

VENDOR = gstin("29AABCU9603R1Z")
BUYER = gstin("27AAPFU0939F1Z")
PARTY = Party(gstins=frozenset({BUYER}), names=("SYNTHETIC BUYER PVT LTD",))
PAGE_TEXT = "\n".join(
    (
        "TAX INVOICE",
        "Invoice No. SYN-INV-0042 Dated 05/08/2026",
        f"GSTIN {VENDOR}",
        f"Bill to Synthetic Buyer Pvt Ltd GSTIN {BUYER}",
        "Synthetic widget 2 500.00",
        "Taxable value 1OOO.OO",
        "Grand total 1,180.00",
    )
)


def _field(value: Any) -> FieldValue[Any]:
    return FieldValue(
        value=value, confidence=Decimal("0.99"), source=FieldSource.RULE, evidence=FieldEvidence()
    )


def _truth() -> Truth:
    document = TruthDocument(
        doc_id="S1-INV",
        set_id="S1",
        doc_type=DocType.INVOICE,
        files=("F1",),
        header={
            "doc_no": "SYN-INV-0042",
            "doc_date": "2026-08-05",
            "vendor_gstin": VENDOR,
            "taxable_total": "1000.00",
            "cgst": "90.00",
            "grand_total": "1180.00",
        },
        lines=({"quantity": "2", "amount": "500.00", "description": "Synthetic widget"},),
        links=(),
        expected_status=("unlinked",),
    )
    return Truth(
        run_date=date(2026, 9, 1),
        buyer_entities=({"name": "Synthetic Buyer Pvt Ltd", "gstin": BUYER},),
        files=(TruthFile("F1", "f1.pdf", DocType.INVOICE),),
        documents=(document,),
        digest="synthetic",
    )


def _result() -> ParseResult:
    header = InvoiceHeader(
        doc_no=_field("SYN-INV-0042"),
        doc_date=_field(date(2026, 5, 8)),  # day and month swapped
        vendor_gstin=_field(BUYER),  # the buyer's own GSTIN
    )
    return ParseResult(
        success=True,
        file_format=FileFormat.PDF,
        documents=(Document(header=header, lines=(), needs_verification=True),),
        warnings=(ParseWarning(code="OCR_LAYOUT_EXTRACTION", message="synthetic"),),
        layout_signature="synthetic",
        page_texts=(PageText(page=1, content=PAGE_TEXT, scanned=True, is_ocr=True),),
    )


def test_each_miss_is_attributed_to_its_stage() -> None:
    scan_pages = {"F1": [{"kind": "scan", "rotate": 0, "skew": 0.2, "dpi": 300}]}

    data = attribute(_truth(), {"F1": _result()}, scan_pages).as_json()

    row = data["documents"][0]
    assert row["scan_type"] == "scan-clean"
    assert row["reader"] == "layout-ocr"
    # doc_no correct; vendor = buyer (C); date swapped (E); taxable only with O for 0
    # (A-glyph); cgst nowhere (A-absent); grand total printed but not read (B).
    assert row["header"] == {"A-absent": 1, "A-glyph": 1, "B": 1, "C": 1, "E": 1}
    assert row["lines"] == {"B": 3}
    scan = data["scan_pages"]
    assert scan["documents"] == 1
    assert scan["header"] == {
        "ok": 1,
        "total": 6,
        "misses": {"A": 2, "B": 1, "C": 1, "D": 0, "E": 1},
    }
    assert scan["lines"]["amount"] == {
        "ok": 0,
        "total": 1,
        "misses": {"A": 0, "B": 1, "C": 0, "D": 0, "E": 0},
    }
    examples = {item["pattern"]: item["examples"] for item in data["patterns"]}
    assert examples["cgst:missing:A-absent"] == ["truth=DD.DD got=-"]


def test_value_only_on_another_page_of_a_split_file_is_a_page_split_miss() -> None:
    def stage(split: bool) -> str:
        return header_stage(
            "doc_no",
            "missing",
            "SYN-INV-0042",
            None,
            doc_text="TAX INVOICE page two",
            file_text="Invoice No. SYN-INV-0042\nTAX INVOICE page two",
            split=split,
            party=PARTY,
        )

    assert stage(True) == "D"
    assert stage(False) == "A-absent"


def test_unexpected_value_is_a_rules_miss_unless_it_is_the_buyer() -> None:
    def stage(extracted: str) -> str:
        return header_stage(
            "vendor_gstin",
            "unexpected",
            None,
            extracted,
            doc_text=PAGE_TEXT,
            file_text=PAGE_TEXT,
            split=False,
            party=PARTY,
        )

    assert stage(BUYER) == "C"
    assert stage(VENDOR) == "B"


@pytest.mark.parametrize(
    ("pages", "label"),
    [
        ([{"kind": "digital"}], "digital"),
        ([{"kind": "scan", "rotate": 0, "skew": 0.1, "dpi": 300}], "scan-clean"),
        ([{"kind": "scan", "rotate": 0, "skew": 1.5, "dpi": 300}], "scan-tilted"),
        ([{"kind": "scan", "rotate": 0, "skew": 7.0, "dpi": 300}], "scan-skew>5"),
        ([{"kind": "scan", "rotate": 0, "skew": 0.0, "dpi": 150}], "scan-lowdpi"),
        (
            [{"kind": "digital"}, {"kind": "scan", "rotate": 180, "skew": 0, "dpi": 300}],
            "scan-rotated",
        ),
    ],
)
def test_scan_label(pages: list[dict[str, Any]], label: str) -> None:
    assert scan_label(pages) == label
