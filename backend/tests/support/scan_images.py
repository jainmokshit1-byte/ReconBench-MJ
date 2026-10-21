"""Synthetic scanned-invoice PDFs for OCR tests (scan fix plan WI-2).

A US-letter page is rendered as a PIL image at ``dpi`` and embedded full-bleed
with reportlab, so the PDF has no text layer unless ``text_layer`` asks for an
invisible one. Distortions mimic real scans: ``skew_deg`` (small tilt),
``rotate`` (quarter turns), ``jpeg_quality`` (lossy re-encode), ``noise``
(fraction of pixels flipped, seeded), and ``tiles`` (the page image is split
into N horizontal strips, as some scanners write it).

``text_layer``: ``none`` = image only; ``good`` = invisible text matching the
image; ``poor`` = invisible text whose characters are garbled, like a broken
scanner OCR layer. Values are synthetic only; never put real document text here.
"""

from __future__ import annotations

import io
import random
import shutil
import subprocess
from functools import cache
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from tests.support.layouts import gstin

TextLayer = Literal["none", "poor", "good"]

# Geometry of the original fixture (1700 x 2200 px = US letter at 200 DPI).
_BASE_DPI = 200
_LEFT_PX = 110
_TOP_PX = 120
_LINE_STEP_PX = 170
_FONT_PX = 42

VENDOR_GSTIN = gstin("27AAPCS1234A1Z")


@cache
def tesseract_with_english() -> bool:
    """True when the ``tesseract`` binary and its English data are installed.

    OCR tests skip on machines without it; CI installs it so they always run.
    """

    executable = shutil.which("tesseract")
    if executable is None:
        return False
    languages = subprocess.run(
        [executable, "--list-langs"], capture_output=True, check=True, text=True, timeout=10
    ).stdout
    return "eng" in languages.split()


def synthetic_invoice_lines(
    number: str = "SYN-INV-0001", *, vendor_gstin: str = VENDOR_GSTIN
) -> tuple[str, ...]:
    """Label/value lines of a small synthetic tax invoice (taxable 200, total 236)."""

    return (
        "TAX INVOICE",
        f"Invoice Number: {number}",
        "Invoice Date: 06/04/2026",
        "Supplier: Synthetic Components Pvt Ltd",
        f"Supplier GSTIN: {vendor_gstin}",
        "PO Reference: SYN-PO-0001",
        "Taxable Value: 200.00",
        "CGST: 18.00",
        "SGST: 18.00",
        "Total Value: 236.00",
    )


def _garble(text: str) -> str:
    """Swap letters and digits for look-alike junk, keeping spacing and length."""

    table = str.maketrans(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
        "#@(|)E=6|\\<1^~0PQ%5+UVW*Y2#6(|)e=9|j<1^~0pq%5+uvw*y2O|ZE4S6T8g",
    )
    return text.translate(table)


def render_invoice_image(
    lines: tuple[str, ...],
    *,
    dpi: int = _BASE_DPI,
    skew_deg: float = 0.0,
    rotate: int = 0,
    noise: float = 0.0,
    seed: int = 7,
) -> Image.Image:
    """Render ``lines`` on a white letter page and apply the scan distortions."""

    if rotate % 90:
        raise ValueError("rotate must be a multiple of 90 degrees")
    scale = dpi / _BASE_DPI
    width, height = round(8.5 * dpi), round(11 * dpi)
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=round(_FONT_PX * scale))
    for index, line in enumerate(lines):
        position = (round(_LEFT_PX * scale), round((_TOP_PX + index * _LINE_STEP_PX) * scale))
        draw.text(position, line, fill=0, font=font)
    if skew_deg:
        image = image.rotate(
            skew_deg, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255
        )
    if noise:
        rng = random.Random(seed)
        pixels = image.load()
        assert pixels is not None
        for _ in range(round(width * height * noise)):
            x, y = rng.randrange(width), rng.randrange(height)
            pixels[x, y] = 0 if rng.random() < 0.5 else 255
    if rotate % 360:
        image = image.rotate(rotate, expand=True)
    return image


def _encode(image: Image.Image, jpeg_quality: int | None) -> ImageReader:
    if jpeg_quality is None:
        return ImageReader(image)
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=jpeg_quality)
    stream.seek(0)
    return ImageReader(stream)


def scanned_invoice_pdf(
    lines: tuple[str, ...] | None = None,
    *,
    skew_deg: float = 0.0,
    rotate: int = 0,
    dpi: int = _BASE_DPI,
    jpeg_quality: int | None = None,
    noise: float = 0.0,
    tiles: int = 1,
    text_layer: TextLayer = "none",
) -> bytes:
    """A one-page image PDF of a synthetic invoice with the given scan defects."""

    if tiles < 1:
        raise ValueError("tiles must be at least 1")
    lines = synthetic_invoice_lines() if lines is None else lines
    image = render_invoice_image(lines, dpi=dpi, skew_deg=skew_deg, rotate=rotate, noise=noise)
    page_w, page_h = letter if image.width < image.height else (letter[1], letter[0])
    points_per_px = 72 / dpi

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(page_w, page_h))
    strip_px = -(-image.height // tiles)
    for top in range(0, image.height, strip_px):
        strip = image.crop((0, top, image.width, min(top + strip_px, image.height)))
        strip_h = strip.height * points_per_px
        y = page_h - top * points_per_px - strip_h
        pdf.drawImage(_encode(strip, jpeg_quality), 0, y, width=page_w, height=strip_h)
    if text_layer != "none":
        scale = dpi / _BASE_DPI
        size = _FONT_PX * scale * points_per_px
        for index, line in enumerate(lines):
            text = pdf.beginText()
            text.setTextRenderMode(3)  # invisible, like an OCR layer
            text.setFont("Helvetica", size)
            baseline_px = (_TOP_PX + index * _LINE_STEP_PX) * scale + _FONT_PX * scale
            text.setTextOrigin(
                _LEFT_PX * scale * points_per_px, page_h - baseline_px * points_per_px
            )
            text.textOut(line if text_layer == "good" else _garble(line))
            pdf.drawText(text)
    pdf.showPage()
    pdf.save()
    return stream.getvalue()


__all__ = [
    "VENDOR_GSTIN",
    "TextLayer",
    "render_invoice_image",
    "scanned_invoice_pdf",
    "synthetic_invoice_lines",
    "tesseract_with_english",
]
