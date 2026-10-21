"""Synthetic image-scan OCR tests (scan fix plan WI-2).

Each case renders a synthetic invoice image with one scan defect and reads it
through the sandboxed extractor with real Tesseract. They skip only where
Tesseract is missing; CI installs it so they always run there.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from reconbench_app.pdf_sandbox import parse_pdf_isolated
from reconbench_core.parsers.types import ParseResult, ParserHints
from reconbench_core.schema import DocType, FileFormat
from tests.support.scan_images import VENDOR_GSTIN, scanned_invoice_pdf, tesseract_with_english

pytestmark = pytest.mark.skipif(
    not tesseract_with_english(), reason="Tesseract with English data is not installed"
)

_EXPECTED: dict[str, object] = {
    "doc_no": "SYN-INV-0001",
    "doc_date": date(2026, 4, 6),
    "grand_total": Decimal("236"),
    "taxable_total": Decimal("200"),
    "vendor_gstin": VENDOR_GSTIN,
}


def _read(**scan: Any) -> ParseResult:
    result = parse_pdf_isolated(
        scanned_invoice_pdf(**scan),
        ParserHints(doc_type=DocType.INVOICE, file_format=FileFormat.PDF),
        languages=("eng",),
    )
    assert result.success
    assert len(result.documents) == 1
    assert len(result.page_texts) == 1 and result.page_texts[0].is_ocr
    return result


def _values(result: ParseResult) -> dict[str, object]:
    header = result.documents[0].header
    return {
        name: (field.value if (field := getattr(header, name)) is not None else None)
        for name in _EXPECTED
    }


@pytest.mark.parametrize(
    "scan",
    [
        pytest.param({"skew_deg": 1.5}, id="skew-1.5deg"),
        pytest.param({"dpi": 150}, id="low-dpi-150"),
        pytest.param({"rotate": 90}, id="rotated-90"),
        pytest.param({"tiles": 4}, id="four-strips"),
    ],
)
def test_common_scan_defects_still_read_every_header_field(scan: dict[str, Any]) -> None:
    result = _read(**scan)

    assert _values(result) == _EXPECTED
    # An OCR-only document always goes to review, however clean the reading.
    assert result.documents[0].needs_verification


def test_garbled_text_layer_does_not_replace_the_ocr_reading() -> None:
    result = _read(text_layer="poor")

    assert _values(result) == _EXPECTED
    assert result.documents[0].needs_verification


@pytest.mark.parametrize(
    "scan",
    [
        pytest.param({"jpeg_quality": 60, "noise": 0.003}, id="jpeg60-light-noise"),
        pytest.param({"jpeg_quality": 40, "noise": 0.01}, id="jpeg40-heavy-noise"),
    ],
)
def test_lossy_noisy_scan_reads_correct_values_or_none(scan: dict[str, Any]) -> None:
    result = _read(**scan)

    values = _values(result)
    # Recall may drop on a degraded page (WI-8), but a wrong value never appears.
    assert all(values[name] in (None, expected) for name, expected in _EXPECTED.items())
    assert values["grand_total"] == _EXPECTED["grand_total"]
    assert result.documents[0].needs_verification
