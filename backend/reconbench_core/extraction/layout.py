"""Geometry of positioned words: pages, visual lines and simple spatial queries.

Coordinates are integers in a page's own pixel space (top-left origin). The
engine that produced the words decides the scale; only relative positions
matter here. Confidence is 0–100 as reported by OCR; text-layer words use 100.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class PageSource(StrEnum):
    TEXT_LAYER = "text_layer"
    OCR = "ocr"


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    conf: int = 100

    @property
    def cx(self) -> int:
        return (self.x0 + self.x1) // 2

    @property
    def cy(self) -> int:
        return (self.y0 + self.y1) // 2

    @property
    def height(self) -> int:
        return max(1, self.y1 - self.y0)


@dataclass(frozen=True, slots=True)
class Rule:
    """A detected printed line: horizontal when ``y0 == y1``, else vertical."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def horizontal(self) -> bool:
        return self.y0 == self.y1


@dataclass(frozen=True, slots=True)
class PageLayout:
    number: int
    width: int
    height: int
    words: tuple[Word, ...]
    source: PageSource
    rules: tuple[Rule, ...] = ()
    rotation: int = 0

    @property
    def median_conf(self) -> int:
        if not self.words:
            return 0
        values = sorted(word.conf for word in self.words)
        return values[len(values) // 2]


@dataclass(frozen=True, slots=True)
class TextLine:
    page: int
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def x0(self) -> int:
        return min(word.x0 for word in self.words)

    @property
    def x1(self) -> int:
        return max(word.x1 for word in self.words)

    @property
    def y0(self) -> int:
        return min(word.y0 for word in self.words)

    @property
    def y1(self) -> int:
        return max(word.y1 for word in self.words)

    @property
    def cy(self) -> int:
        return (self.y0 + self.y1) // 2

    def words_between(self, x0: int, x1: int) -> tuple[Word, ...]:
        """Words whose horizontal centre lies in ``[x0, x1)``."""

        return tuple(word for word in self.words if x0 <= word.cx < x1)


@dataclass(frozen=True, slots=True)
class DocumentLayout:
    pages: tuple[PageLayout, ...]
    page_count: int = field(default=0)

    def page(self, number: int) -> PageLayout | None:
        return next((page for page in self.pages if page.number == number), None)


def typical_height(words: Iterable[Word]) -> int:
    heights = sorted(word.height for word in words)
    if not heights:
        return 10
    return heights[len(heights) // 2]


def group_lines(page: PageLayout, *, gap_factor: int = 2) -> tuple[TextLine, ...]:
    """Group words into visual lines by vertical overlap, left to right.

    A word joins a line when its vertical centre is within half a typical word
    height of the line's centre. Lines are split where the horizontal gap
    exceeds ``gap_factor`` typical heights, so far-apart columns stay separate
    *segments* that share a ``cy`` but not a ``TextLine``.
    """

    if not page.words:
        return ()
    height = typical_height(page.words)
    tolerance = max(2, height // 2)
    rows: list[list[Word]] = []
    centres: list[int] = []
    for word in sorted(page.words, key=lambda item: (item.cy, item.x0)):
        for index in range(len(rows) - 1, max(-1, len(rows) - 4), -1):
            if abs(centres[index] - word.cy) <= tolerance:
                rows[index].append(word)
                centres[index] = sum(item.cy for item in rows[index]) // len(rows[index])
                break
        else:
            rows.append([word])
            centres.append(word.cy)
    lines: list[TextLine] = []
    for row in rows:
        row.sort(key=lambda item: item.x0)
        segment: list[Word] = [row[0]]
        for word in row[1:]:
            if word.x0 - segment[-1].x1 > gap_factor * height:
                lines.append(TextLine(page.number, tuple(segment)))
                segment = [word]
            else:
                segment.append(word)
        lines.append(TextLine(page.number, tuple(segment)))
    lines.sort(key=lambda line: (line.cy, line.x0))
    return tuple(lines)


def full_rows(page: PageLayout) -> tuple[TextLine, ...]:
    """Lines that keep every word sharing a baseline (no column splitting)."""

    return group_lines(page, gap_factor=10_000)


def same_row(a: TextLine | Word, b: TextLine | Word, height: int) -> bool:
    return abs(a.cy - b.cy) <= max(2, height // 2)


def nearest_below(
    lines: Sequence[TextLine], anchor: TextLine | Word, *, max_rows: int, height: int
) -> list[TextLine]:
    """Lines directly below ``anchor`` that overlap it horizontally, closest first."""

    x0, x1 = anchor.x0, anchor.x1
    below = [
        line
        for line in lines
        if line.y0 > anchor.cy + height // 2
        and line.y0 - anchor.y1 <= max_rows * height * 2
        and line.x1 >= x0 - height
        and line.x0 <= x1 + height * 4
    ]
    below.sort(key=lambda line: (line.y0, abs(line.x0 - x0)))
    return below


def layout_to_dict(layout: DocumentLayout) -> dict[str, object]:
    return {
        "page_count": layout.page_count,
        "pages": [
            {
                "number": page.number,
                "width": page.width,
                "height": page.height,
                "source": page.source.value,
                "rotation": page.rotation,
                "words": [
                    [word.text, word.x0, word.y0, word.x1, word.y1, word.conf]
                    for word in page.words
                ],
                "rules": [[rule.x0, rule.y0, rule.x1, rule.y1] for rule in page.rules],
            }
            for page in layout.pages
        ],
    }


def layout_from_dict(data: dict[str, object]) -> DocumentLayout:
    pages = []
    for raw in data["pages"]:  # type: ignore[attr-defined]
        pages.append(
            PageLayout(
                number=int(raw["number"]),
                width=int(raw["width"]),
                height=int(raw["height"]),
                source=PageSource(raw["source"]),
                rotation=int(raw.get("rotation", 0)),
                words=tuple(
                    Word(str(t), int(a), int(b), int(c), int(d), int(e))
                    for t, a, b, c, d, e in raw["words"]
                ),
                rules=tuple(Rule(int(a), int(b), int(c), int(d)) for a, b, c, d in raw["rules"]),
            )
        )
    return DocumentLayout(pages=tuple(pages), page_count=int(data.get("page_count", len(pages))))  # type: ignore[call-overload]


__all__ = [
    "DocumentLayout",
    "PageLayout",
    "PageSource",
    "Rule",
    "TextLine",
    "Word",
    "full_rows",
    "layout_from_dict",
    "layout_to_dict",
    "group_lines",
    "nearest_below",
    "same_row",
    "typical_height",
]
