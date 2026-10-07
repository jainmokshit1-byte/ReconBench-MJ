"""Find QR codes on PDF pages (zxing-cpp); payload parsing lives in the core.

Runs inside the PDF sandbox. Pages are rendered at 300 DPI, the resolution at
which scanned e-invoice QRs decode reliably; only the first pages are read
because the portal QR is printed on the first page (sometimes repeated).
"""

from __future__ import annotations

from importlib import metadata

import pypdfium2  # type: ignore[import-untyped]
import zxingcpp
from reconbench_core.extraction.qr import MAX_QR_TEXT

QR_DPI = 300
QR_MAX_PAGES = 5
_POINTS_PER_INCH = 72


def decode_qr_texts(content: bytes, *, max_pages: int = QR_MAX_PAGES) -> list[tuple[int, str]]:
    """``(page number, text)`` for every QR code found on the first ``max_pages`` pages."""

    found: list[tuple[int, str]] = []
    document = pypdfium2.PdfDocument(content)
    try:
        for index in range(min(len(document), max_pages)):
            image = document[index].render(scale=QR_DPI / _POINTS_PER_INCH).to_pil()
            for barcode in zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode):
                if barcode.valid and barcode.text and len(barcode.text) <= MAX_QR_TEXT:
                    found.append((index + 1, barcode.text))
    finally:
        document.close()
    return found


def decoder_version() -> str:
    return metadata.version("zxing-cpp")


__all__ = ["QR_DPI", "QR_MAX_PAGES", "decode_qr_texts", "decoder_version"]
