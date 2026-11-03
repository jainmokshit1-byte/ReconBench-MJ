"""The rules table reader keeps serial numbers from an "S/N" row under an item."""

from __future__ import annotations

import pytest
from reconbench_core.extraction.layout import PageLayout, PageSource, Word
from reconbench_core.extraction.table import extract_tables, serial_tokens


def _row(y: int, *cells: tuple[int, str]) -> list[Word]:
    words: list[Word] = []
    for x, text in cells:
        x0 = x
        for part in text.split(" "):
            words.append(Word(part, x0, y, x0 + len(part) * 14, y + 26, 96))
            x0 += len(part) * 14 + 12
    return words


def _page(*extra_rows: list[Word]) -> PageLayout:
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
            (300, "Laptop Charger"),
            (1100, "85044030"),
            (1400, "2 No"),
            (1700, "500.00"),
            (2150, "1,000.00"),
        ),
        *extra_rows,
        _row(1300, (700, "Total"), (1400, "2 No"), (2150, "1,000.00")),
    ]
    return PageLayout(1, 2480, 3505, tuple(w for row in rows for w in row), PageSource.TEXT_LAYER)


def test_a_serial_row_is_captured_and_the_description_is_unchanged() -> None:
    page = _page(_row(830, (300, "S/N: SYN12345678, SYN87654321")))

    [item] = extract_tables([page]).items

    assert item.serials == ["SYN12345678", "SYN87654321"]
    assert item.description_rows == ["Laptop Charger"]


def test_serials_continue_on_a_bare_row_and_a_description_row_ends_them() -> None:
    page = _page(
        _row(830, (300, "Serial No SYN12345678")),
        _row(870, (300, "SYN87654321")),
    )

    [item] = extract_tables([page]).items

    assert item.serials == ["SYN12345678", "SYN87654321"]


def test_an_item_without_a_serial_row_has_none() -> None:
    [item] = extract_tables([_page()]).items

    assert item.serials == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("S/N: AB1234, CD56789", ["AB1234", "CD56789"]),
        ("SN SYN12345678", ["SYN12345678"]),
        ("Serial No: ABCDEFGH", []),  # no digit
        ("Serial No: AB12", []),  # too short
        ("Type: Laptop", None),
        ("SYN12345678", None),
    ],
)
def test_serial_token_rules(text: str, expected: list[str] | None) -> None:
    assert serial_tokens(text) == expected
