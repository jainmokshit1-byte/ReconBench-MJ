"""Synthetic PDF parser checks; no user-supplied documents are opened."""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reconbench_app.pdf_sandbox import parse_pdf_isolated
from reconbench_core.parsers import parse
from reconbench_core.parsers import pdf as pdf_parser
from reconbench_core.parsers.types import ParserHints
from reconbench_core.schema import DocType, FileFormat, ReasonCode
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle


def _searchable_invoice_pdf() -> bytes:
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    fields = (
        "Invoice Number: SYN-INV-001",
        "Order Date: 2026-03-25",
        "Invoice Date: 2026-04-01",
        "Supplier: Acme Components Pvt Ltd",
        "Vendor GSTIN: 27AAPFU0939F1ZV",
        "PO Reference: SYN-PO-0142",
        "Taxable Value: 200.00",
        "CGST: 18.00",
        "SGST: 18.00",
        "Total Value: 236.00",
    )
    for page_index, item in enumerate(("Widget A", "Widget B")):
        page.setFont("Helvetica", 10)
        for row_index, line in enumerate(fields):
            page.drawString(36, 748 - row_index * 17, line)
        table = Table(
            [
                ["Line No", "Description", "HSN", "UOM", "Quantity", "Rate", "Amount"],
                [str(page_index + 1), item, "8471", "NOS", "1", "100.00", "100.00"],
            ],
            colWidths=[44, 160, 45, 42, 58, 58, 62],
        )
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                ]
            )
        )
        _width, height = table.wrapOn(page, 500, 120)
        table.drawOn(page, 36, 560 - height)
        if page_index == 0:
            page.showPage()
    page.save()
    return stream.getvalue()


def _unruled_invoice_pdf() -> bytes:
    """Build searchable line items as positioned text without table borders."""
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    page.setFont("Helvetica", 9)
    headings = (
        (36, "Line No"),
        (100, "Description"),
        (270, "HSN"),
        (330, "Qty"),
        (380, "Rate"),
        (450, "Amount"),
    )
    for x, value in headings:
        page.drawString(x, 650, value)
    rows = (
        (
            (36, "1"),
            (100, "Copper Wire"),
            (270, "7408"),
            (330, "2"),
            (380, "100.00"),
            (450, "200.00"),
        ),
        (
            (36, "2"),
            (100, "Steel Fasteners"),
            (270, "7318"),
            (330, "3"),
            (380, "20.00"),
            (450, "60.00"),
        ),
    )
    for row_index, cells in enumerate(rows):
        for x, value in cells:
            page.drawString(x, 620 - row_index * 24, value)
    page.save()
    return stream.getvalue()


def _split_header_unruled_invoice_pdf() -> bytes:
    """Build an unruled table whose headings occupy two nearby baselines."""
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    page.setFont("Helvetica", 9)
    for x, value in ((36, "Line No"), (100, "Description"), (270, "HSN")):
        page.drawString(x, 650, value)
    for x, value in ((330, "Qty"), (380, "Rate"), (450, "Amount")):
        page.drawString(x, 642, value)
    rows = (
        (
            (36, "1"),
            (100, "Copper Wire"),
            (270, "7408"),
            (330, "2"),
            (380, "100.00"),
            (450, "200.00"),
        ),
        (
            (36, "2"),
            (100, "Steel Fasteners"),
            (270, "7318"),
            (330, "3"),
            (380, "20.00"),
            (450, "60.00"),
        ),
    )
    for row_index, cells in enumerate(rows):
        for x, value in cells:
            page.drawString(x, 620 - row_index * 24, value)
    page.save()
    return stream.getvalue()


def _side_by_side_invoice_header_pdf() -> bytes:
    """Build a searchable header with document number/date in adjacent columns."""
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    page.setFont("Helvetica", 10)
    page.drawString(300, 700, "Invoice No.")
    page.drawString(440, 700, "Dated")
    page.drawString(300, 685, "SYN-INV-2026-001")
    page.drawString(440, 685, "28-Aug-26")
    page.save()
    return stream.getvalue()


def _ambiguous_invoice_reference_pdf() -> bytes:
    """Exercise blank-label artifacts and distinguish GRN, challan, and PO refs."""
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    page.setFont("Helvetica", 10)
    for index, line in enumerate(
        (
            "Invoice Number: REF-INV-001",
            "Invoice Date: 2026-09-01",
            "Supplier: Acme Components Pvt Ltd",
            "Vendor GSTIN: 27AAPFU0939F1ZV",
            "Buyer's Order No: &",
            "Delivery Note:",
            "PADAMJI ROAD GRANT ROAD EAST MUMBAI",
            "PO/26-27/08-210",
            "MRN No: MRN-7781",
            "Delivery Note No: DC-4402",
        )
    ):
        page.drawString(36, 748 - index * 17, line)
    page.save()
    return stream.getvalue()


def _invoice_with_serial_left_of_description_pdf() -> bytes:
    """Build a searchable item row where data begins left of its text heading."""
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    page.setFont("Helvetica", 9)
    for x, value in (
        (40, "Sl"),
        (119, "Description of Goods"),
        (259, "HSN/SAC"),
        (320, "Quantity"),
        (388, "Rate"),
        (522, "Amount"),
    ):
        page.drawString(x, 650, value)
    page.drawString(38, 640, "No.")
    page.drawString(440, 650, "per")
    row = (
        (40, "1"),
        (61, "500"),
        (84, "GB"),
        (103, "SSD"),
        (125, "WD"),
        (145, "(BLUE)-SANDISK"),
        (257, "84717090"),
        (317, "1.00"),
        (331, "NOS"),
        (398, "8,500.00"),
        (439, "NOS"),
        (537, "8,500.00"),
    )
    for x, value in row:
        page.drawString(x, 620, value)
    page.drawString(61, 608, "252510801288")
    page.save()
    return stream.getvalue()


def _invoice_with_party_blocks_and_amount_words_pdf() -> bytes:
    """Build a searchable invoice with unlabelled seller name and buyer GSTIN block."""
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    page.setFont("Helvetica", 9)
    for x, y, value in (
        (42, 750, "ACME COMPONENTS"),
        (300, 750, "Invoice No."),
        (430, 750, "Dated"),
        (300, 735, "INV-GST-001"),
        (430, 735, "28-Aug-26"),
        (42, 720, "GSTIN/UIN : 27AAPFU0939F1ZV"),
        (42, 704, "PO/26-27/08-222"),
        (42, 680, "Buyer (Bill to)"),
        (42, 666, "Example Buyer Limited"),
        (42, 651, "GSTIN/UIN : 29AABCU9603R1ZM"),
        (36, 600, "Sl"),
        (105, 600, "Description"),
        (250, 600, "HSN/SAC"),
        (318, 600, "Quantity"),
        (382, 600, "Rate"),
        (495, 600, "Amount"),
        (36, 580, "1"),
        (60, 580, "Steel Bracket"),
        (250, 580, "7308"),
        (318, 580, "2.00"),
        (382, 580, "100.00"),
        (495, 580, "200.00"),
        (42, 520, "Amount Chargeable (in words)"),
        (42, 505, "Indian Rupees Two Hundred Thirty Six Only"),
        (42, 475, "Tax Amount (in words)"),
        (42, 460, "Indian Rupees Thirty Six Only"),
    ):
        page.drawString(x, y, value)
    page.drawString(60, 568, "SKU-123456")
    page.save()
    return stream.getvalue()


def _scanned_pdf() -> bytes:
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    image = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(image)
    draw.text(
        (100, 180),
        "SYNTHETIC SCANNED INVOICE",
        fill="black",
        font=ImageFont.load_default(),
    )
    image = image.filter(ImageFilter.GaussianBlur(radius=1.2))
    page.drawImage(ImageReader(image), 36, 36, width=540, height=720)
    page.showPage()
    page.save()
    return stream.getvalue()


def _scanned_invoice_pdf() -> bytes:
    """Build a readable image-only invoice fixture without a hidden text layer."""
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    image = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=42)
    for index, line in enumerate(
        (
            "TAX INVOICE",
            "Invoice Number: OCR-SYN-2026-001",
            "Order Date: 25.03.2026",
            "Invoice Date: 06/04/2026",
            "Supplier: Acme Components Pvt Ltd",
            "Supplier GSTIN: 27AAPFU09319ZFV",
            "PO Reference: PO-TEST-100",
            "Taxable Value: 200.00",
            "CGST: 18.00",
            "SGST: 18.00",
            "Total Value: 236.00",
        )
    ):
        draw.text((110, 120 + index * 170), line, fill="black", font=font)
    page.drawImage(ImageReader(image), 36, 36, width=540, height=720)
    page.showPage()
    page.save()
    return stream.getvalue()


def _encrypted_pdf() -> bytes:
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter, encrypt=StandardEncryption("synthetic"))
    page.drawString(36, 748, "Invoice No: SYN-LOCKED")
    page.save()
    return stream.getvalue()


def test_pdf_text_extraction_keeps_fields_lines_pages_and_bounding_boxes() -> None:
    result = parse(
        _searchable_invoice_pdf(),
        {"file_format": FileFormat.PDF, "doc_type": DocType.INVOICE},
    )

    assert result.success
    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.header.doc_no is not None
    assert document.header.doc_no.value == "SYN-INV-001"
    assert document.header.doc_no.evidence.page == 1
    assert document.header.doc_no.evidence.bbox is not None
    assert document.header.doc_date is not None
    assert document.header.doc_date.value.isoformat() == "2026-04-01"
    assert document.header.grand_total is not None
    assert document.header.grand_total.value == 236
    assert document.header.vendor_name is not None
    assert document.header.vendor_name.value == "Acme Components Pvt Ltd"
    assert len(document.lines) == 2
    assert document.lines[0].description is not None
    assert document.lines[0].description.value == "Widget A"
    assert document.lines[1].description is not None
    assert document.lines[1].description.value == "Widget B"
    assert document.lines[0].description.evidence.page == 1
    assert document.lines[1].description.evidence.page == 2
    assert all(page.content and not page.scanned for page in result.page_texts)


def test_pdf_text_extraction_reads_unruled_text_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_parser, "_extract_tables", lambda *_args: ([], set()))
    result = parse(
        _unruled_invoice_pdf(),
        {"file_format": FileFormat.PDF, "doc_type": DocType.INVOICE},
    )

    assert result.success
    assert len(result.documents) == 1
    lines = result.documents[0].lines
    assert len(lines) == 2
    assert lines[0].description is not None
    assert lines[0].description.value == "Copper Wire"
    assert lines[0].hsn_sac is not None
    assert lines[0].hsn_sac.value == "7408"
    assert lines[0].quantity is not None
    assert lines[0].quantity.value == 2
    assert lines[0].amount is not None
    assert lines[0].amount.value == 200
    assert lines[1].description is not None
    assert lines[1].description.value == "Steel Fasteners"


def test_pdf_text_extraction_combines_nearby_header_baselines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_parser, "_extract_tables", lambda *_args: ([], set()))
    result = parse(
        _split_header_unruled_invoice_pdf(),
        {"file_format": FileFormat.PDF, "doc_type": DocType.INVOICE},
    )

    assert result.success
    lines = result.documents[0].lines
    assert len(lines) == 2
    assert lines[0].description is not None
    assert lines[0].description.value == "Copper Wire"
    assert lines[1].description is not None
    assert lines[1].description.value == "Steel Fasteners"
    first_box = lines[0].description.evidence.bbox
    second_box = lines[1].description.evidence.bbox
    assert first_box is not None and second_box is not None
    assert first_box[1] != second_box[1]


def test_pdf_header_reads_values_below_adjacent_invoice_number_and_dated_labels() -> None:
    result = parse(
        _side_by_side_invoice_header_pdf(),
        {"file_format": FileFormat.PDF, "doc_type": DocType.INVOICE},
    )

    assert result.success
    header = result.documents[0].header
    assert header.doc_no is not None
    assert header.doc_no.value == "SYN-INV-2026-001"
    assert header.doc_date is not None
    assert header.doc_date.value.isoformat() == "2026-08-28"


def test_pdf_item_row_recovers_description_overflow_and_rate_uom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_parser, "_extract_tables", lambda *_args: ([], set()))

    result = parse(
        _invoice_with_serial_left_of_description_pdf(),
        {"file_format": FileFormat.PDF, "doc_type": DocType.INVOICE},
    )

    assert result.success
    assert len(result.documents[0].lines) == 1
    line = result.documents[0].lines[0]
    assert line.description is not None
    assert line.description.value == "500 GB SSD WD (BLUE)-SANDISK"
    assert line.item_code is not None and line.item_code.value == "252510801288"
    assert line.hsn_sac is not None and line.hsn_sac.value == "84717090"
    assert line.quantity is not None and line.quantity.value == 1
    assert line.uom is not None and line.uom.value == "NOS"
    assert line.rate is not None and line.rate.value == 8500
    assert line.amount is not None and line.amount.value == 8500


def test_pdf_extracts_party_gstins_po_reference_and_total_in_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_parser, "_extract_tables", lambda *_args: ([], set()))
    result = parse(
        _invoice_with_party_blocks_and_amount_words_pdf(),
        {"file_format": FileFormat.PDF, "doc_type": DocType.INVOICE},
    )

    assert result.success
    document = result.documents[0]
    assert document.header.vendor_name is not None
    assert document.header.vendor_name.value == "ACME COMPONENTS"
    assert document.header.vendor_gstin is not None
    assert document.header.vendor_gstin.value == "27AAPFU0939F1ZV"
    assert document.header.buyer_gstin is not None
    assert document.header.buyer_gstin.value == "29AABCU9603R1ZM"
    assert document.header.po_refs is not None
    assert document.header.po_refs.value == ("PO/26-27/08-222",)
    assert document.header.grand_total is not None
    assert document.header.grand_total.value == 236
    assert (
        document.header.grand_total.evidence.snippet == "Indian Rupees Two Hundred Thirty Six Only"
    )
    assert document.needs_verification


def test_pdf_reference_fields_reject_artifacts_and_keep_reference_types_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_parser, "_extract_tables", lambda *_args: ([], set()))
    result = parse(
        _ambiguous_invoice_reference_pdf(),
        {"file_format": FileFormat.PDF, "doc_type": DocType.INVOICE},
    )

    assert result.success
    header = result.documents[0].header
    assert header.po_refs is not None
    assert header.po_refs.value == ("PO/26-27/08-210",)
    assert header.grn_refs is not None
    assert header.grn_refs.value == ("MRN-7781",)
    assert header.challan_no is not None
    assert header.challan_no.value == "DC-4402"


def test_pdf_without_text_becomes_hash_keyed_review_shell() -> None:
    content = _scanned_pdf()
    result = parse(content, {"file_format": "pdf", "doc_type": "invoice"})

    assert result.success
    assert result.warnings[0].code == "NO_TEXT_LAYER"
    document = result.documents[0]
    assert document.needs_verification
    assert ReasonCode.NO_TEXT_LAYER in document.reason_codes
    assert document.natural_key is not None
    assert document.natural_key == hashlib.sha256(content).hexdigest()
    assert document.header.doc_no is None
    assert len(result.page_texts) == 1 and result.page_texts[0].scanned


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_scanned_invoice_ocr_extracts_evidence_and_requires_review() -> None:
    executable = shutil.which("tesseract")
    assert executable is not None
    languages = subprocess.run(
        [executable, "--list-langs"], capture_output=True, check=True, text=True, timeout=10
    ).stdout
    if "eng" not in languages.split():
        pytest.skip("Tesseract English trained data is not installed")

    result = parse_pdf_isolated(
        _scanned_invoice_pdf(),
        ParserHints(doc_type=DocType.INVOICE, file_format=FileFormat.PDF),
        languages=("eng",),
    )

    assert result.success
    assert len(result.documents) == 1
    assert len(result.page_texts) == 1
    assert result.page_texts[0].is_ocr
    assert "Invoice Number: OCR-SYN-2026-001" in result.page_texts[0].content
    document = result.documents[0]
    assert document.header.doc_no is not None
    assert document.header.doc_no.value == "OCR-SYN-2026-001"
    assert document.header.doc_no.evidence.page == 1
    assert document.header.doc_no.evidence.bbox is not None
    # An OCR-read number is trusted only after an independent second reading agrees.
    assert document.header.doc_no.confidence < 0.80 or any(
        warning.code == "DUAL_READ_CONFIRMED" for warning in result.warnings
    )
    assert document.header.doc_date is not None
    assert document.header.doc_date.value.isoformat() == "2026-04-06"
    assert document.header.grand_total is not None
    assert document.header.grand_total.value == 236
    assert document.header.taxable_total is not None
    assert document.header.taxable_total.value == 200
    assert document.needs_verification
    assert any(warning.code == "GSTIN_INVALID" for warning in result.warnings)
    assert any(warning.code == "OCR_REVIEW_REQUIRED" for warning in result.warnings)


def test_pdf_parser_rejects_bad_bytes_and_page_limit() -> None:
    invalid = parse(b"not a PDF", {"file_format": "pdf", "doc_type": "invoice"})
    page_limited = parse(
        _searchable_invoice_pdf(),
        {"file_format": "pdf", "doc_type": "invoice", "max_pages": 1},
    )

    assert not invalid.success and invalid.reason_code == "INVALID_PDF"
    assert not page_limited.success and page_limited.reason_code == "PDF_PAGE_LIMIT"


def test_password_pdf_is_rejected_without_prompting_for_a_password() -> None:
    result = parse(_encrypted_pdf(), {"file_format": "pdf", "doc_type": "invoice"})

    assert not result.success
    assert result.reason_code == "ENCRYPTED_PDF"


@pytest.mark.parametrize(
    "payload",
    [
        b"%PDF-1.7",
        b"%PDF-1.7\x00garbage\xff",
        b"%PDF-1.4\n1 0 obj<</Length 1000000000>>stream",
    ],
)
def test_malformed_pdf_like_inputs_are_rejected(payload: bytes) -> None:
    result = parse(payload, {"file_format": "pdf", "doc_type": "invoice"})

    assert not result.success
    assert result.reason_code in {"INVALID_PDF", "EMPTY_PDF"}
