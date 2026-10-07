"""Page types and document boundaries inside one PDF (accuracy plan AC-3).

A file can hold several documents: a bundle of PO, GRN and invoice pages, or two
invoices scanned together. Each page is classified by its printed title, then
consecutive pages are grouped into documents:

* a new document starts where the title type changes (invoice → PO);
* pages of the same type split only on hard evidence: two text-layer pages with
  different document numbers, or a "Page 1 of N" after a finished page run;
* e-way bills and pages without a title (continuations, annexures, terms) stay
  with the document before them.

Multi-page scans repeat their title ("TAX INVOICE") on every page and OCR reads
their numbers differently page to page, so OCR pages of one type never split
on a number alone. Only strong title phrases count; a word such as "Invoice"
in "Invoice To" or "Invoice No." is a label, not a title.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from reconbench_core.extraction.header import norm
from reconbench_core.extraction.layout import PageLayout, PageSource, TextLine, typical_height
from reconbench_core.schema import DocType, Document


class PageType(StrEnum):
    INVOICE = "invoice"
    PO = "po"
    GRN = "grn"
    EWAY_BILL = "eway_bill"
    # No title: a continuation, annexure or terms page of the document before it.
    CONTINUATION = "continuation"


_DOCUMENT_TYPES = {
    PageType.INVOICE: DocType.INVOICE,
    PageType.PO: DocType.PO,
    PageType.GRN: DocType.GRN,
}

# A title phrase is a label when a number label word or a value follows it
# ("Purchase Order No.", "Tax Invoice 4500123"), or when a reference word comes
# before it ("Against Purchase Order", "Invoice To").
_LABEL_AFTER = r"(?!\s+(?:NO|NUMBER|NUM|DATE|DT|DATED|REF|REFERENCE|TO|ID|\S*\d))"
_REFERENCE_BEFORE = r"(?<!AGAINST )(?<!YOUR )(?<!OUR )(?<!BUYER )(?<!BUYERS )(?<!CUSTOMER )"
_TITLES: tuple[tuple[PageType, re.Pattern[str]], ...] = (
    (
        PageType.PO,
        re.compile(rf"{_REFERENCE_BEFORE}\bPURCHASE ORDER\b{_LABEL_AFTER}"),
    ),
    (
        PageType.GRN,
        re.compile(
            rf"{_REFERENCE_BEFORE}\b(?:GOODS RECEIPT NOTE|GOODS RECEIVED NOTE|GOODS RECEIPT|"
            rf"MATERIAL RECEIPT NOTE|MATERIAL RECEIPT|RECEIPT NOTE|INWARD NOTE)\b{_LABEL_AFTER}"
        ),
    ),
    (
        PageType.INVOICE,
        re.compile(
            rf"{_REFERENCE_BEFORE}\b(?:TAX ?INVOICE|BILL OF SUPPLY|CASH MEMO|RETAIL INVOICE|"
            rf"COMMERCIAL INVOICE)\b{_LABEL_AFTER}"
        ),
    ),
    # A row that is only the word, as when OCR splits "TAX" and "INVOICE" apart.
    (PageType.INVOICE, re.compile(r"^INVOICE$")),
)
# Short heading rows such as "PO - Extracted Record" or "GRN".
_SHORT_HEADING = re.compile(r"^(PO|GRN)\b(?!\s+(?:NO|NUMBER|DATE|DT|REF|REFERENCE|\S*\d))")
_SHORT_HEADING_TOKENS = 4
_PAGE_OF = re.compile(r"\bPAGE\s*(\d{1,3})\s*(?:OF|/)\s*(\d{1,3})\b")
# Titles sit in the top part of a page; lower rows are body text.
_TITLE_BAND = 0.40
_LARGE_FONT = 1.3


@dataclass(frozen=True, slots=True)
class PageInfo:
    number: int
    type: PageType
    source: PageSource
    # (x, y) from a printed "Page x of y", when one was read.
    page_of: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class PageGroup:
    """Consecutive pages that form one document of ``doc_type``."""

    doc_type: DocType
    pages: tuple[int, ...]


def _row_text(text: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]", " ", text.upper()).split())


def title_type(rows: Sequence[tuple[str, float]]) -> PageType | None:
    """The document type named by the strongest title among ``(text, relative height)`` rows.

    Rows are in reading order; a larger font wins, then the earlier row.
    """

    best: tuple[float, int, PageType] | None = None
    for index, (raw, size) in enumerate(rows):
        text = _row_text(raw)
        if not text:
            continue
        found: PageType | None = None
        for page_type, pattern in _TITLES:
            if pattern.search(text):
                found = page_type
                break
        if found is None:
            heading = _SHORT_HEADING.match(text)
            if heading and len(text.split()) <= _SHORT_HEADING_TOKENS:
                found = PageType.PO if heading.group(1) == "PO" else PageType.GRN
        if found is None:
            continue
        rank = (size, -index, found)
        if best is None or rank[:2] > best[:2]:
            best = rank
    return None if best is None else best[2]


def is_eway_bill(page: PageLayout) -> bool:
    """An e-way bill carries "e-Way Bill" as a large title near the top.

    Invoices only mention it as a small label ("E-WAY BILL NO").
    """

    height = typical_height(page.words)
    top = [w for w in page.words if w.y1 <= page.height * 0.25 and w.conf >= 50]
    for word in top:
        if norm(word.text) not in {"EWAY", "EWAYBILL"} or word.height < height * 1.4:
            continue
        if norm(word.text) == "EWAYBILL":
            return True
        for other in top:
            if (
                norm(other.text) == "BILL"
                and abs(other.cy - word.cy) <= word.height
                and 0 <= other.x0 - word.x1 <= 3 * word.height
            ):
                return True
    return False


def _page_of(rows: Sequence[TextLine]) -> tuple[int, int] | None:
    for row in rows:
        match = _PAGE_OF.search(_row_text(row.text))
        if match:
            current, total = int(match.group(1)), int(match.group(2))
            if 1 <= current <= total:
                return current, total
    return None


def classify_page(page: PageLayout, rows: Sequence[TextLine]) -> PageInfo:
    page_of = _page_of(rows)
    if is_eway_bill(page):
        return PageInfo(page.number, PageType.EWAY_BILL, page.source, page_of)
    height = typical_height(page.words)
    band = [row for row in rows if row.y0 <= page.height * _TITLE_BAND]
    sized = [
        (
            row.text,
            1.0 if max(word.height for word in row.words) < height * _LARGE_FONT else 2.0,
        )
        for row in band
    ]
    found = title_type(sized)
    return PageInfo(page.number, found or PageType.CONTINUATION, page.source, page_of)


def _starts_new_document(
    previous: PageInfo,
    page: PageInfo,
    doc_type: DocType,
    number_of: Callable[[int, DocType], str | None],
) -> bool:
    # "Page 1 of N" after a finished run ("Page 2 of 2", or no page numbers).
    if (
        page.page_of is not None
        and page.page_of[0] == 1
        and (previous.page_of is None or previous.page_of[0] == previous.page_of[1])
    ):
        return True
    if previous.source is PageSource.TEXT_LAYER and page.source is PageSource.TEXT_LAYER:
        before, after = number_of(previous.number, doc_type), number_of(page.number, doc_type)
        return before is not None and after is not None and before != after
    return False


def group_pages(
    pages: Sequence[PageInfo],
    zone: DocType,
    number_of: Callable[[int, DocType], str | None] = lambda _page, _type: None,
) -> tuple[PageGroup, ...]:
    """Split classified pages into documents.

    ``number_of(page, doc_type)`` returns a page's own document number in a
    comparable (look-alike folded) form, or ``None``; it is asked only for
    text-layer pages of the same type. A file without any titled page is one
    document of the upload ``zone`` type.
    """

    if not pages:
        return ()
    typed = [page for page in pages if page.type in _DOCUMENT_TYPES]
    if not typed:
        return (PageGroup(zone, tuple(page.number for page in pages)),)
    groups: list[tuple[DocType, list[int]]] = []
    current_type = _DOCUMENT_TYPES[typed[0].type]
    pending: list[int] = []
    last_titled: PageInfo | None = None
    for page in pages:
        if page.type not in _DOCUMENT_TYPES:
            pending.append(page.number)
            continue
        doc_type = _DOCUMENT_TYPES[page.type]
        if last_titled is None:
            pending.append(page.number)
            last_titled = page
            continue
        if doc_type is not current_type or _starts_new_document(
            last_titled, page, doc_type, number_of
        ):
            groups.append((current_type, pending))
            current_type, pending = doc_type, []
        pending.append(page.number)
        last_titled = page
    groups.append((current_type, pending))
    return tuple(PageGroup(doc_type, tuple(numbers)) for doc_type, numbers in groups)


def is_plain_split(groups: Sequence[PageGroup], zone: DocType) -> bool:
    """One document of the upload zone's type: nothing for a person to confirm."""

    return len(groups) <= 1 and all(group.doc_type is zone for group in groups)


def split_pages(document: Document) -> tuple[int, ...]:
    """Source pages of a document split out of a multi-document file; empty otherwise."""

    marker = document.header.extras.get("page_split")
    if not isinstance(marker, dict):
        return ()
    return tuple(int(page) for page in marker.get("pages", ()))


def split_unconfirmed(document: Document) -> bool:
    """The document came from a proposed split that no person has accepted yet.

    Until then it is never verified, never linked automatically and never Matched.
    """

    marker = document.header.extras.get("page_split")
    return isinstance(marker, dict) and marker.get("state") != "confirmed"


def text_page_types(contents: Sequence[str]) -> tuple[PageType | None, ...]:
    """Title type of each page from plain text alone (no positions or font sizes).

    Used to decide whether a digital PDF needs the layout path to split it; only
    the first lines of each page are read.
    """

    types: list[PageType | None] = []
    for content in contents:
        lines = [line for line in content.splitlines() if line.strip()][:12]
        types.append(title_type([(line, 1.0) for line in lines]))
    return tuple(types)


__all__ = [
    "PageGroup",
    "PageInfo",
    "PageType",
    "classify_page",
    "group_pages",
    "is_eway_bill",
    "is_plain_split",
    "split_pages",
    "split_unconfirmed",
    "text_page_types",
    "title_type",
]
