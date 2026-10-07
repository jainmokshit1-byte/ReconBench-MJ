"""Synthetic documents and a recorded fake answer for the vision reader (AC-5).

Everything here is invented: the GSTINs are checksum-valid but made up, and the
bank, phone and e-mail details exist only so that masking can be tested. Used
by the unit/integration tests (no provider) and by ``scripts/vision_probe.py``.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass
from typing import Any

from reconbench_core.normalize.gstin import calculate_gstin_checksum

SUPPLIER_GSTIN_BODY = "27AAPFU0939F1Z"
BUYER_GSTIN_BODY = "27AABCS1234Q1Z"
SUPPLIER_GSTIN = SUPPLIER_GSTIN_BODY + calculate_gstin_checksum(SUPPLIER_GSTIN_BODY)
BUYER_GSTIN = BUYER_GSTIN_BODY + calculate_gstin_checksum(BUYER_GSTIN_BODY)
SUPPLIER_NAME = "ACME SYNTHETIC TRADERS"
BUYER_NAME = "SYNTHETIC BUYER PRIVATE LIMITED"
INVOICE_NO = "SYN-INV-0042"
INVOICE_DATE = "02/04/2026"
PO_NO = "PO/26-27/04-901"
HANDWRITTEN_PO = "PO 26-27/04-777"
ACCOUNT_NO = "123456789012"
IFSC = "SBIN0001234"
PHONE = "9876543210"
EMAIL = "accounts@synthetic.example"
LINES = (
    ("Synthetic Laptop Model X1", "8471", "2", "50000.00", "100000.00"),
    ("Synthetic Mouse M2", "8471", "10", "500.00", "5000.00"),
)
TAXABLE = "105000.00"
CGST = "9450.00"
SGST = "9450.00"
GRAND = "123900.00"

# Page size in points (A4) and where each known value was drawn (points, top-left origin).
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0


@dataclass(frozen=True)
class DrawnText:
    name: str
    text: str
    x: float
    y: float  # baseline, from the top
    size: float

    def box_1000(self, width: float) -> list[int]:
        """``[ymin, xmin, ymax, xmax]`` on the 0–1000 page scale."""

        top = self.y - self.size
        bottom = self.y + self.size * 0.25
        return [
            round(top / PAGE_HEIGHT * 1000),
            round(self.x / PAGE_WIDTH * 1000),
            round(bottom / PAGE_HEIGHT * 1000),
            round((self.x + width) / PAGE_WIDTH * 1000),
        ]


def _layout() -> list[DrawnText]:
    texts = [
        DrawnText("title", "TAX INVOICE", 240, 50, 16),
        DrawnText("supplier_name", SUPPLIER_NAME, 40, 85, 12),
        DrawnText("supplier_gstin", f"GSTIN: {SUPPLIER_GSTIN}", 40, 105, 10),
        DrawnText("phone", f"Ph: {PHONE}", 40, 120, 10),
        DrawnText("email", f"Email: {EMAIL}", 40, 135, 10),
        DrawnText("doc_no", f"Invoice No: {INVOICE_NO}", 360, 85, 10),
        DrawnText("doc_date", f"Invoice Date: {INVOICE_DATE}", 360, 100, 10),
        DrawnText("po_ref", f"Buyer's Order No: {PO_NO}", 360, 115, 10),
        DrawnText("buyer_name", f"Bill To: {BUYER_NAME}", 40, 170, 10),
        DrawnText("buyer_gstin", f"GSTIN: {BUYER_GSTIN}", 40, 185, 10),
        DrawnText("header", "Description            HSN    Qty    Rate        Amount", 40, 230, 10),
    ]
    y = 250.0
    for description, hsn, quantity, rate, amount in LINES:
        texts.append(
            DrawnText(
                "line",
                f"{description:<26} {hsn:<6} {quantity:<6} {rate:<11} {amount}",
                40,
                y,
                10,
            )
        )
        y += 18
    texts += [
        DrawnText("taxable_total", f"Taxable Value: {TAXABLE}", 360, 330, 10),
        DrawnText("cgst_total", f"CGST @ 9%: {CGST}", 360, 345, 10),
        DrawnText("sgst_total", f"SGST @ 9%: {SGST}", 360, 360, 10),
        DrawnText("grand_total", f"Grand Total: {GRAND}", 360, 380, 11),
        DrawnText("bank", f"Bank A/c No: {ACCOUNT_NO}  IFSC: {IFSC}", 40, 420, 10),
        DrawnText("for", f"for {SUPPLIER_NAME}", 360, 450, 10),
        DrawnText("signatory", "Authorised Signatory", 380, 540, 10),
    ]
    return texts


def synthetic_invoice_pdf() -> bytes:
    """A one-page digital (text-layer) invoice with fake private details and a handwritten note."""

    from reportlab.lib import colors
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    pdf.setTitle("synthetic vision invoice")
    for item in _layout():
        font = "Helvetica-Bold" if item.name in {"title", "grand_total"} else "Helvetica"
        if item.name == "header" or item.name == "line":
            font = "Courier"
        pdf.setFont(font, item.size)
        pdf.drawString(item.x, PAGE_HEIGHT - item.y, item.text)
    # A scribbled signature and a handwritten-looking PO number in blue ink.
    pdf.setStrokeColor(colors.black)
    pdf.bezier(
        390,
        PAGE_HEIGHT - 520,
        420,
        PAGE_HEIGHT - 480,
        450,
        PAGE_HEIGHT - 530,
        480,
        PAGE_HEIGHT - 495,
    )
    pdf.setFillColor(colors.blue)
    pdf.setFont("Helvetica-Oblique", 13)
    pdf.saveState()
    pdf.translate(60, PAGE_HEIGHT - 470)
    pdf.rotate(4)
    pdf.drawString(0, 0, HANDWRITTEN_PO)
    pdf.restoreState()
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def scan_like_pdf(content: bytes, *, dpi: int = 200, angle: float = 1.5, seed: int = 7) -> bytes:
    """An image-only copy of page 1: rendered, slightly rotated, with light noise."""

    import pypdfium2  # type: ignore[import-untyped]
    from PIL import Image

    document = pypdfium2.PdfDocument(content)
    try:
        image = document[0].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        document.close()
    image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))
    rng = random.Random(seed)
    pixels = image.load()
    assert pixels is not None
    for _ in range(image.width * image.height // 400):
        x, y = rng.randrange(image.width), rng.randrange(image.height)
        shade = rng.randrange(150, 230)
        pixels[x, y] = (shade, shade, shade)
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=dpi)
    return buffer.getvalue()


def known_boxes() -> dict[str, tuple[str, list[int]]]:
    """Field name → (value as printed, expected box) for the digital invoice."""

    from reportlab.pdfbase.pdfmetrics import stringWidth

    values = {
        "supplier_name": SUPPLIER_NAME,
        "supplier_gstin": SUPPLIER_GSTIN,
        "doc_no": INVOICE_NO,
        "doc_date": INVOICE_DATE,
        "po_ref": PO_NO,
        "buyer_gstin": BUYER_GSTIN,
        "taxable_total": TAXABLE,
        "cgst_total": CGST,
        "sgst_total": SGST,
        "grand_total": GRAND,
    }
    boxes: dict[str, tuple[str, list[int]]] = {}
    for item in _layout():
        if item.name not in values:
            continue
        value = values[item.name]
        prefix = item.text[: item.text.index(value)]
        font = "Helvetica-Bold" if item.name == "grand_total" else "Helvetica"
        start = item.x + stringWidth(prefix, font, item.size)
        width = stringWidth(value, font, item.size)
        shifted = DrawnText(item.name, value, start, item.y, item.size)
        boxes[item.name] = (value, shifted.box_1000(width))
    return boxes


def recorded_response(*, page: int = 1) -> dict[str, Any]:
    """A provider answer for the digital invoice, as a correct reader would give it."""

    boxes = known_boxes()
    fields: list[dict[str, Any]] = []
    for name, (text, box) in boxes.items():
        item: dict[str, Any] = {"name": name, "text": text, "page": page, "box_2d": box}
        if name == "po_ref":
            item["label"] = "Buyer's Order No"
        fields.append(item)
    lines = []
    top = 250
    for description, hsn, quantity, rate, amount in LINES:
        y0 = round((top - 10) / PAGE_HEIGHT * 1000)
        y1 = round((top + 3) / PAGE_HEIGHT * 1000)
        lines.append(
            {
                "page": page,
                "box_2d": [y0, 60, y1, 700],
                "description": description,
                "hsn_sac": hsn,
                "quantity": quantity,
                "rate": rate,
                "amount": amount,
            }
        )
        top += 18
    return {
        "doc_kind": "invoice",
        "pages": [{"page": page, "kind": "invoice", "title": "TAX INVOICE"}],
        "fields": fields,
        "lines": lines,
        "annotations": [
            {
                "kind": "handwritten",
                "text": HANDWRITTEN_PO,
                "looks_like": "po_number",
                "page": page,
                "box_2d": [545, 95, 570, 290],
            }
        ],
    }


__all__ = [
    "BUYER_GSTIN",
    "EMAIL",
    "GRAND",
    "INVOICE_NO",
    "PHONE",
    "SUPPLIER_GSTIN",
    "known_boxes",
    "recorded_response",
    "scan_like_pdf",
    "synthetic_invoice_pdf",
]
