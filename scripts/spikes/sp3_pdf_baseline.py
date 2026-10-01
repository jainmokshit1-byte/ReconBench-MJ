"""Synthetic text-layer extraction smoke spike; no real PDFs are opened."""

from __future__ import annotations

import io
import re

import pdfplumber
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

EXPECTED = [
    "Invoice No: SYN-INV-001",
    "Invoice Date: 2026-09-28",
    "PO Number: SYN-PO-0142",
    "Quantity: 10",
    "Rate: INR 48000.00",
    "Invoice Total: INR 566400.00",
]


def create_synthetic_pdf() -> bytes:
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=letter)
    page.setFont("Helvetica", 12)
    y = 740
    for line in EXPECTED:
        page.drawString(72, y, line)
        y -= 24
    page.save()
    return stream.getvalue()


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    pdf_bytes = create_synthetic_pdf()
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        extracted = normalize(" ".join(page.extract_text() or "" for page in pdf.pages))
    missing = [line for line in EXPECTED if normalize(line) not in extracted]
    if missing:
        print(f"Synthetic text layer did not preserve fields: {missing}")
        return 1
    print(
        f"PASS: {len(EXPECTED)}/{len(EXPECTED)} synthetic text fields "
        "extracted exactly; real-layout accuracy remains unmeasured."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
