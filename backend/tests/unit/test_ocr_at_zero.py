"""OCR reads the digit 0 as "@" inside product codes of scanned item descriptions."""

from __future__ import annotations

import pytest
from reconbench_core.extraction.layout import PageLayout, PageSource, Word
from reconbench_core.extraction.table import extract_tables, fix_ocr_at_zero


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PA-142@", "PA-1420"),
        ("5@W LED Bulb", "50W LED Bulb"),
        ("SR1@GB Pen Drive", "SR10GB Pen Drive"),
        ("PA-@42", "PA-042"),
        ("1@@5 Cable", "1005 Cable"),
        ("Cable @ Rs", "Cable @ Rs"),
        ("Input IGST @ 18%", "Input IGST @ 18%"),
        ("Cable IGST@18%", "Cable IGST@18%"),
        ("Cable", "Cable"),
    ],
)
def test_at_sign_touching_a_digit_becomes_zero(raw: str, expected: str) -> None:
    assert fix_ocr_at_zero(raw) == expected


def _row(y: int, *cells: tuple[int, str]) -> list[Word]:
    words: list[Word] = []
    for x, text in cells:
        x0 = x
        for part in text.split(" "):
            words.append(Word(part, x0, y, x0 + len(part) * 14, y + 26, 96))
            x0 += len(part) * 14 + 12
    return words


def _page(source: PageSource) -> PageLayout:
    rows = [
        _row(
            700,
            (100, "Sl"),
            (300, "Description of Goods"),
            (1100, "HSN/SAC"),
            (1400, "Quantity"),
            (1700, "Rate"),
            (2150, "Amount"),
        ),
        _row(
            780,
            (100, "1"),
            (300, "SR1@GB Pen Drive"),
            (1100, "84733010"),
            (1400, "2 No"),
            (1700, "500.00"),
            (2150, "1,000.00"),
        ),
        _row(1100, (700, "Total"), (1400, "2 No"), (2150, "1,000.00")),
    ]
    return PageLayout(1, 2480, 3505, tuple(w for row in rows for w in row), source)


def _description(source: PageSource) -> str:
    items = extract_tables([_page(source)]).items
    assert len(items) == 1
    return " ".join([items[0].item_code or "", *items[0].description_rows]).strip()


def test_ocr_item_description_is_repaired() -> None:
    assert _description(PageSource.OCR) == "SR10GB Pen Drive"


def test_text_layer_item_description_is_left_alone() -> None:
    assert _description(PageSource.TEXT_LAYER) == "SR1@GB Pen Drive"
