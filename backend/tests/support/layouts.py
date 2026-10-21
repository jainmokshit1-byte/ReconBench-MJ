"""Synthetic page layouts for rule tests (scan fix plan WI-2).

Rule tests build positioned words directly, so they need no OCR and are
deterministic. A page is a list of rows; each row holds cells ``(x, text)``
whose words are laid out left to right at a fixed character width:

    page(
        row(100, (1000, "TAX INVOICE")),
        row(300, (100, "GSTIN: 29AABCU9603R1ZJ"), conf=60),
    )

``word`` gives full control over one box (text, x, y, w, h, conf). Values are
synthetic only; never put real document text here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from reconbench_core.extraction.layout import DocumentLayout, PageLayout, PageSource, Rule, Word
from reconbench_core.normalize import calculate_gstin_checksum

PAGE_WIDTH = 2480
PAGE_HEIGHT = 3505
CHAR_WIDTH = 14
WORD_HEIGHT = 26
WORD_GAP = 12
OCR_CONF = 96


def word(text: str, x: int, y: int, w: int, h: int = WORD_HEIGHT, conf: int = OCR_CONF) -> Word:
    """One word box from a table spec ``(text, x, y, w, h, conf)``."""

    return Word(text, x, y, x + w, y + h, conf)


def words(spec: Iterable[tuple[str, int, int, int, int, int]]) -> list[Word]:
    return [word(*item) for item in spec]


def row(y: int, *cells: tuple[int, str], conf: int = OCR_CONF) -> list[Word]:
    """Words of one visual line; each cell's text is split on spaces from ``x``."""

    out: list[Word] = []
    for x, text in cells:
        x0 = x
        for part in text.split(" "):
            if not part:
                continue
            width = len(part) * CHAR_WIDTH
            out.append(Word(part, x0, y, x0 + width, y + WORD_HEIGHT, conf))
            x0 += width + WORD_GAP
    return out


def hrule(y: int, x0: int = 80, x1: int = PAGE_WIDTH - 80) -> Rule:
    return Rule(x0, y, x1, y)


def vrule(x: int, y0: int, y1: int) -> Rule:
    return Rule(x, y0, x, y1)


def page(
    *rows: Sequence[Word],
    number: int = 1,
    source: PageSource = PageSource.OCR,
    rules: Sequence[Rule] = (),
    width: int = PAGE_WIDTH,
    height: int = PAGE_HEIGHT,
) -> PageLayout:
    return PageLayout(
        number,
        width,
        height,
        tuple(item for line in rows for item in line),
        source,
        tuple(rules),
    )


def document(*pages: PageLayout) -> DocumentLayout:
    return DocumentLayout(tuple(pages), len(pages))


def gstin(body: str) -> str:
    """A 15-character GSTIN with a valid check character for a 14-character body."""

    return body + calculate_gstin_checksum(body)


__all__ = [
    "CHAR_WIDTH",
    "OCR_CONF",
    "PAGE_HEIGHT",
    "PAGE_WIDTH",
    "WORD_HEIGHT",
    "document",
    "gstin",
    "hrule",
    "page",
    "row",
    "vrule",
    "word",
    "words",
]
