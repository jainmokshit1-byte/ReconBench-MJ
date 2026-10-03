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
    assert document.header.doc_no.confidence < 0.80
    assert document.header.doc_date is not None
    assert document.header.doc_date.value.isoformat() == "2026-04-06"
    assert document.header.grand_total is not None
    assert document.header.grand_total.value == 236
    assert document.header.taxable_total is not None
    assert document.header.taxable_total.value == 200
    assert document.needs_verification
    assert any(warning.code == "GSTIN_INVALID" for warning in result.warnings)


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
