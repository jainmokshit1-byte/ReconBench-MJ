"""Deterministic redaction (``redaction.v1``) with a map back to the original text.

Matches are replaced by placeholders such as ``[EMAIL_1]``; equal values share a
placeholder. The span map converts a position in the sent text back to the
original page so grounded quotes can be shown at their true location.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

REDACTION_VERSION = "redaction.v1"

Category = Literal["email", "phone", "bank_account", "ifsc", "gstin", "pan"]
# GSTIN stays in H1 text because it is an extracted invoice field.
H1_CATEGORIES: tuple[Category, ...] = ("email", "phone", "bank_account", "ifsc", "pan")
STRICT_CATEGORIES: tuple[Category, ...] = (
    "email",
    "phone",
    "bank_account",
    "ifsc",
    "gstin",
    "pan",
)

_PATTERNS: dict[Category, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "gstin": re.compile(r"(?<![0-9A-Za-z])\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z](?![0-9A-Za-z])"),
    "pan": re.compile(r"(?<![0-9A-Za-z])[A-Z]{5}\d{4}[A-Z](?![0-9A-Za-z])"),
    "ifsc": re.compile(r"(?<![0-9A-Za-z])[A-Z]{4}0[A-Z0-9]{6}(?![0-9A-Za-z])"),
    "phone": re.compile(
        r"(?<![\d])(?:(?:\+|00)91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\d])"
        r"|(?<![\d])0\d{2,4}[\s-]\d{6,8}(?![\d])"
    ),
    # Account numbers are only recognised after an account label, so invoice
    # numbers and amounts are not mistaken for them.
    "bank_account": re.compile(
        r"(?i)(?:\ba/?c\b|\baccount\b|\bacct\b)(?:\s*(?:no|number|#))?\.?\s*[:\-]?\s*"
        r"(?P<value>\d[\d -]{6,22}\d)"
    ),
}
PLACEHOLDER = re.compile(r"\[(?:EMAIL|PHONE|BANK_ACCOUNT|IFSC|GSTIN|PAN)_\d+\]")


@dataclass(frozen=True, slots=True)
class RedactionSpan:
    sent_start: int
    sent_end: int
    original_start: int
    original_end: int
    placeholder: str


@dataclass(frozen=True, slots=True)
class RedactedText:
    original: str
    text: str
    spans: tuple[RedactionSpan, ...]

    def to_original(self, sent_start: int, sent_end: int) -> tuple[int, int] | None:
        """Map a sent-text range to the original; ``None`` if it touches a placeholder."""

        if not 0 <= sent_start <= sent_end <= len(self.text):
            return None
        shift = 0
        for span in self.spans:
            if sent_end <= span.sent_start:
                break
            if sent_start < span.sent_end:
                return None
            shift = span.original_end - span.sent_end
        return sent_start + shift, sent_end + shift

    @property
    def redaction_count(self) -> int:
        return len(self.spans)


def _matches(text: str, categories: Iterable[Category]) -> list[tuple[int, int, Category]]:
    found: list[tuple[int, int, Category]] = []
    for category in categories:
        for match in _PATTERNS[category].finditer(text):
            if category == "bank_account":
                start, end = match.span("value")
            else:
                start, end = match.span()
            found.append((start, end, category))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, Category]] = []
    last_end = -1
    for start, end, category in found:
        if start >= last_end:
            selected.append((start, end, category))
            last_end = end
    return selected


def redact(
    text: str,
    categories: Iterable[Category],
    *,
    numbering: dict[tuple[Category, str], str] | None = None,
) -> RedactedText:
    """Redact ``text``; a shared ``numbering`` keeps placeholders stable across pages."""

    labels = numbering if numbering is not None else {}
    pieces: list[str] = []
    spans: list[RedactionSpan] = []
    cursor = 0
    sent_length = 0
    for start, end, category in _matches(text, tuple(categories)):
        before = text[cursor:start]
        pieces.append(before)
        sent_length += len(before)
        value = text[start:end]
        key = (category, value)
        if key not in labels:
            count = sum(1 for existing in labels if existing[0] == category) + 1
            labels[key] = f"[{category.upper()}_{count}]"
        placeholder = labels[key]
        pieces.append(placeholder)
        spans.append(
            RedactionSpan(
                sent_start=sent_length,
                sent_end=sent_length + len(placeholder),
                original_start=start,
                original_end=end,
                placeholder=placeholder,
            )
        )
        sent_length += len(placeholder)
        cursor = end
    pieces.append(text[cursor:])
    return RedactedText(original=text, text="".join(pieces), spans=tuple(spans))


def contains_placeholder(value: str) -> bool:
    return PLACEHOLDER.search(value) is not None


# --------------------------------------------------------------------------- page images
#
# Page images (h6_vision) are masked on word boxes from the same OCR or text layer
# the parser used. The categories follow H1 plus UPI ids and signatures. GSTINs,
# names, addresses, document numbers, dates, amounts and handwriting stay visible:
# they are what the reader must read. This is best effort: words OCR cannot read
# are not masked, which the consent text states.

IMAGE_MASK_VERSION = "image_mask.v1"
ImageMaskCategory = Literal["email", "upi", "phone", "bank_account", "ifsc", "pan", "signature"]
MASK_PADDING = 6

_GSTIN_WORD = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]")
_UPI = re.compile(r"(?<![\w.])[\w.\-]{2,}@[A-Za-z]{2,}(?![\w.@])")
_IFSC_LOOSE = re.compile(r"(?<![0-9A-Za-z])[A-Z]{4}[0O][A-Z0-9]{6}(?![0-9A-Za-z])")
_PHONE_LABEL = re.compile(
    r"(?i)^(?:ph|phone|phones|tel|telephone|mob|mobile|cell|contact|fax|whatsapp|m)\.?:?$"
)
_ACCOUNT_LABEL = re.compile(r"(?i)^(?:a/?c|acct|account|ac)\.?:?$")
_IFSC_LABEL = re.compile(r"(?i)^(?:ifsc|ifs|rtgs/neft|neft/rtgs)\.?:?$")
_PAN_LABEL = re.compile(r"^(?:PAN|Pan|pan)\.?:?$")
_FILLER = re.compile(r"(?i)^(?:no|no\.|number|num|nos|#|:|-|code|details?)\.?:?$")
_SIGNATURE = re.compile(r"(?i)\b(?:authori[sz]ed\s+signatory|signatory|signature)\b")
# A "Signature" column heading in an item table is not a signing area.
_TABLE_HEADING = re.compile(r"(?i)\b(?:rate|qty|quantity|amount|hsn|description|particulars)\b")
_SIGNATURE_MAX_HEIGHT = 300  # pixels at 300 DPI: about 25 mm above the label


@dataclass(frozen=True, slots=True)
class WordBox:
    """One word with its box on the page image (pixels)."""

    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    conf: int = 100


@dataclass(frozen=True, slots=True)
class MaskRegion:
    category: ImageMaskCategory
    x0: int
    y0: int
    x1: int
    y1: int


def _rows(words: Sequence[WordBox]) -> list[list[WordBox]]:
    ordered = sorted(
        (word for word in words if word.text.strip()), key=lambda w: (w.y0 + w.y1, w.x0)
    )
    if not ordered:
        return []
    heights = sorted(max(1, word.y1 - word.y0) for word in ordered)
    tolerance = max(4, heights[len(heights) // 2] * 6 // 10)
    rows: list[list[WordBox]] = []
    centres: list[int] = []
    for word in ordered:
        centre = (word.y0 + word.y1) // 2
        if rows and abs(centre - centres[-1]) <= tolerance:
            rows[-1].append(word)
            continue
        rows.append([word])
        centres.append(centre)
    return [sorted(row, key=lambda w: w.x0) for row in rows]


def _row_text(row: Sequence[WordBox]) -> tuple[str, list[tuple[int, int]]]:
    pieces: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for index, word in enumerate(row):
        if index:
            pieces.append(" ")
            cursor += 1
        spans.append((cursor, cursor + len(word.text)))
        pieces.append(word.text)
        cursor += len(word.text)
    return "".join(pieces), spans


def _words_in(spans: Sequence[tuple[int, int]], start: int, end: int) -> list[int]:
    return [index for index, (left, right) in enumerate(spans) if left < end and start < right]


def _digits(text: str) -> int:
    return sum(char.isdigit() for char in text)


def _numbers_after(row: Sequence[WordBox], index: int, *, min_digits: int) -> list[int]:
    """Value words after a label: skip fillers ("No.", ":"), then digit groups."""

    position = index + 1
    while position < len(row) and _FILLER.match(row[position].text.strip()):
        position += 1
    taken: list[int] = []
    while position < len(row) and len(taken) < 4:
        text = row[position].text.strip(",;")
        if not text or not re.fullmatch(r"[\d+()\-/.,]+", text) or _digits(text) == 0:
            break
        taken.append(position)
        position += 1
    total = sum(_digits(row[i].text) for i in taken)
    return taken if total >= min_digits else []


def _token_after(row: Sequence[WordBox], index: int, pattern: str) -> list[int]:
    position = index + 1
    while position < len(row) and _FILLER.match(row[position].text.strip()):
        position += 1
    if position < len(row) and re.fullmatch(pattern, row[position].text.strip(" :.,;")):
        return [position]
    return []


def _below(
    rows: Sequence[Sequence[WordBox]], row_index: int, label: WordBox, *, min_digits: int
) -> WordBox | None:
    if row_index + 1 >= len(rows):
        return None
    width = label.x1 - label.x0
    for word in rows[row_index + 1]:
        if word.x1 < label.x0 - width or word.x0 > label.x1 + 4 * width:
            continue
        if _digits(word.text) >= min_digits and re.fullmatch(r"[\d\-/ ]+", word.text.strip()):
            return word
    return None


def _signature_band(
    rows: Sequence[Sequence[WordBox]], row_index: int, label: tuple[int, int, int, int]
) -> tuple[int, int, int, int] | None:
    x0, y0, x1, _y1 = label
    margin = (x1 - x0) // 4
    left, right = x0 - margin, x1 + margin
    top = y0 - _SIGNATURE_MAX_HEIGHT
    for row in reversed(rows[:row_index]):
        solid = [
            word
            for word in row
            if word.conf >= 60
            and sum(char.isalnum() for char in word.text) >= 3
            and word.x1 >= left
            and word.x0 <= right
            and word.y1 <= y0
        ]
        if solid:
            top = max(top, max(word.y1 for word in solid) + 2)
            break
    bottom = y0 - 2
    if bottom - top < 20:
        return None
    return left, top, right, bottom


def image_mask_regions(
    words: Sequence[WordBox], *, width: int, height: int, padding: int = MASK_PADDING
) -> tuple[MaskRegion, ...]:
    """Regions to black out on one page image, found from its word boxes."""

    rows = _rows(words)
    found: list[tuple[ImageMaskCategory, tuple[int, int, int, int]]] = []

    def take(category: ImageMaskCategory, row: Sequence[WordBox], indexes: Iterable[int]) -> None:
        for index in indexes:
            word = row[index]
            if category != "email" and _GSTIN_WORD.search(word.text.upper()):
                continue  # a GSTIN is a field to read, never masked
            found.append((category, (word.x0, word.y0, word.x1, word.y1)))

    for row_index, row in enumerate(rows):
        text, spans = _row_text(row)
        for category, pattern in (
            ("email", _PATTERNS["email"]),
            ("upi", _UPI),
            ("phone", _PATTERNS["phone"]),
            ("pan", _PATTERNS["pan"]),
            ("ifsc", _IFSC_LOOSE),
        ):
            for match in pattern.finditer(text):
                take(category, row, _words_in(spans, *match.span()))  # type: ignore[arg-type]
        for match in _PATTERNS["bank_account"].finditer(text):
            take("bank_account", row, _words_in(spans, *match.span("value")))
        for index, word in enumerate(row):
            token = word.text.strip()
            if _PHONE_LABEL.match(token):
                take("phone", row, _numbers_after(row, index, min_digits=6))
            elif _ACCOUNT_LABEL.match(token):
                numbers = _numbers_after(row, index, min_digits=6)
                if numbers:
                    take("bank_account", row, numbers)
                elif all(_FILLER.match(other.text.strip()) for other in row[index + 1 :]):
                    below = _below(rows, row_index, word, min_digits=6)
                    if below is not None:
                        found.append(("bank_account", (below.x0, below.y0, below.x1, below.y1)))
            elif _IFSC_LABEL.match(token):
                take("ifsc", row, _token_after(row, index, r"[A-Za-z0-9]{8,13}"))
            elif _PAN_LABEL.match(token):
                take("pan", row, _token_after(row, index, r"[A-Za-z0-9]{10}"))
        signatures = () if _TABLE_HEADING.search(text) else _SIGNATURE.finditer(text)
        for match in signatures:
            indexes = _words_in(spans, *match.span())
            if not indexes:
                continue
            label = (
                min(row[i].x0 for i in indexes),
                min(row[i].y0 for i in indexes),
                max(row[i].x1 for i in indexes),
                max(row[i].y1 for i in indexes),
            )
            band = _signature_band(rows, row_index, label)
            if band is not None:
                found.append(("signature", band))

    regions: list[MaskRegion] = []
    seen: set[tuple[str, int, int, int, int]] = set()
    for category, (x0, y0, x1, y1) in found:
        box = (
            max(0, x0 - padding),
            max(0, y0 - padding),
            min(width, x1 + padding),
            min(height, y1 + padding),
        )
        if box[2] <= box[0] or box[3] <= box[1] or (category, *box) in seen:
            continue
        seen.add((category, *box))
        regions.append(MaskRegion(category, *box))
    return tuple(sorted(regions, key=lambda r: (r.y0, r.x0, r.category)))


__all__ = [
    "H1_CATEGORIES",
    "IMAGE_MASK_VERSION",
    "MASK_PADDING",
    "PLACEHOLDER",
    "REDACTION_VERSION",
    "STRICT_CATEGORIES",
    "Category",
    "RedactedText",
    "RedactionSpan",
    "ImageMaskCategory",
    "MaskRegion",
    "WordBox",
    "contains_placeholder",
    "image_mask_regions",
    "redact",
]
