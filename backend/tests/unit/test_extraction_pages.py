"""Page split and classify (accuracy plan AC-3) on synthetic pages only.

A bundle file becomes separate documents; a multi-page scan whose title repeats
on every page and whose number OCR reads differently per page stays one
document; label words such as "Invoice To" never decide a page type.
"""

from __future__ import annotations

import pytest
import reconbench_app.extraction.pipeline as pipeline
from reconbench_core.extraction.layout import (
    DocumentLayout,
    PageLayout,
    PageSource,
    Word,
    full_rows,
)
from reconbench_core.extraction.pages import (
    PageGroup,
    PageInfo,
    PageType,
    classify_page,
    group_pages,
    is_plain_split,
    split_pages,
    split_unconfirmed,
    text_page_types,
    title_type,
)
from reconbench_core.parsers.types import PageSplit, ParserHints
from reconbench_core.schema import DocType
from tests.unit.test_extraction_core import VENDOR_GSTIN, _invoice, _page, _row

OCR = PageSource.OCR
TEXT = PageSource.TEXT_LAYER


# --- titles ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ("PURCHASE ORDER", PageType.PO),
        ("PO - Extracted Record", PageType.PO),
        ("GRN", PageType.GRN),
        ("Receipt Note", PageType.GRN),
        ("Goods Received Note", PageType.GRN),
        ("Tax Invoice/Bill of Supply/Cash Memo", PageType.INVOICE),
        ("x0 ~ TAX INVOICE (ORIGINAL FOR RECIPIENT)", PageType.INVOICE),
        ("TAXINVOICE -", PageType.INVOICE),
        ("INVOICE", PageType.INVOICE),
        # Labels, not titles.
        ("Invoice To SYN-PO/99-01", None),
        ("Invoice Voucher No. Dated", None),
        ("Receipt Note No. Dated", None),
        ("PO No.: SYN-PO/99-02", None),
        ("Purchase Order No. 4500012", None),
        ("Delivered against Purchase Order", None),
        ("Tax Invoice No. SYN-1", None),
        ("INVOICE NO. 901", None),
        ("PO 4500012345 dated 01-08-2026", None),
    ],
)
def test_title_phrases_and_label_traps(row: str, expected: PageType | None) -> None:
    assert title_type([(row, 1.0)]) == expected


def test_larger_title_wins_then_the_earlier_row() -> None:
    assert title_type([("PURCHASE ORDER", 1.0), ("TAX INVOICE", 2.0)]) is PageType.INVOICE
    assert title_type([("PURCHASE ORDER", 1.0), ("TAX INVOICE", 1.0)]) is PageType.PO


def test_text_page_types_reads_only_the_page_tops() -> None:
    body = "\n".join(["line"] * 20 + ["TAX INVOICE"])
    assert text_page_types(["PO - Extracted Record\nPO No.: X", "Receipt Note", body]) == (
        PageType.PO,
        PageType.GRN,
        None,
    )


# --- page classification ---------------------------------------------------------------------


def _titled(number: int, title: str, source: PageSource = OCR, *, size: int = 26) -> PageLayout:
    words = [
        Word(part, 900 + 300 * i, 100, 1150 + 300 * i, 100 + size)
        for i, part in enumerate(title.split())
    ]
    body = _row(400, (100, "Synthetic Traders Pvt Ltd"))
    return PageLayout(number, 2480, 3505, tuple(words + body), source)


def test_classify_page_reads_titles_eway_bills_and_page_numbers() -> None:
    po = _titled(1, "PURCHASE ORDER", TEXT)
    eway_title = [Word("e-Way", 900, 120, 1100, 180), Word("Bill", 1130, 120, 1250, 180)]
    eway = _page(2, [eway_title, _row(400, (100, "Document Details: Tax Invoice - SYN-1"))])
    footer = _page(3, [_row(100, (100, "Terms and conditions")), _row(3300, (2000, "Page 2 of 3"))])

    assert classify_page(po, full_rows(po)) == PageInfo(1, PageType.PO, TEXT)
    assert classify_page(eway, full_rows(eway)).type is PageType.EWAY_BILL
    assert classify_page(footer, full_rows(footer)) == PageInfo(
        3, PageType.CONTINUATION, OCR, (2, 3)
    )
    invoice = _invoice().pages[0]
    assert classify_page(invoice, full_rows(invoice)).type is PageType.INVOICE


def test_a_title_low_on_the_page_is_body_text() -> None:
    page = _page(1, [_row(100, (100, "Synthetic Traders")), _row(2500, (100, "PURCHASE ORDER"))])
    assert classify_page(page, full_rows(page)).type is PageType.CONTINUATION


# --- grouping --------------------------------------------------------------------------------


def _info(number: int, page_type: PageType, source: PageSource = OCR, page_of=None) -> PageInfo:
    return PageInfo(number, page_type, source, page_of)


def _groups(groups: tuple[PageGroup, ...]) -> list[tuple[str, tuple[int, ...]]]:
    return [(group.doc_type.value, group.pages) for group in groups]


def test_bundle_splits_at_type_changes_and_text_layer_number_changes() -> None:
    numbers = {1: "5YN-P0/99-01", 2: "5YN-P0/99-02", 3: "6RN/99-01", 4: "6RN/99-02"}
    pages = [
        _info(1, PageType.PO, TEXT),
        _info(2, PageType.PO, TEXT),
        _info(3, PageType.GRN, TEXT),
        _info(4, PageType.GRN, TEXT),
        _info(5, PageType.INVOICE),
        _info(6, PageType.EWAY_BILL),
    ]

    groups = group_pages(pages, DocType.INVOICE, lambda number, _type: numbers.get(number))

    assert _groups(groups) == [
        ("po", (1,)),
        ("po", (2,)),
        ("grn", (3,)),
        ("grn", (4,)),
        ("invoice", (5, 6)),
    ]
    assert not is_plain_split(groups, DocType.INVOICE)


def test_multi_page_scan_with_repeated_titles_stays_one_document() -> None:
    # OCR reads the number differently on each page; that never splits OCR pages.
    numbers = {1: "5YN1D0000042", 2: "5YN100000042", 3: "5YN10000004", 4: None}
    pages = [_info(n, PageType.INVOICE) for n in (1, 2, 3, 4)] + [_info(5, PageType.EWAY_BILL)]

    groups = group_pages(pages, DocType.INVOICE, lambda number, _type: numbers[number])

    assert _groups(groups) == [("invoice", (1, 2, 3, 4, 5))]
    assert is_plain_split(groups, DocType.INVOICE)


def test_page_one_after_a_finished_run_starts_a_new_document() -> None:
    two_invoices = [
        _info(1, PageType.INVOICE, page_of=(1, 1)),
        _info(2, PageType.INVOICE, page_of=(1, 1)),
    ]
    one_invoice = [
        _info(1, PageType.INVOICE, page_of=(1, 2)),
        _info(2, PageType.INVOICE, page_of=(2, 2)),
    ]
    misread = [
        _info(1, PageType.INVOICE, page_of=(1, 4)),
        _info(2, PageType.INVOICE, page_of=(1, 4)),
    ]

    assert _groups(group_pages(two_invoices, DocType.INVOICE)) == [
        ("invoice", (1,)),
        ("invoice", (2,)),
    ]
    assert _groups(group_pages(one_invoice, DocType.INVOICE)) == [("invoice", (1, 2))]
    assert _groups(group_pages(misread, DocType.INVOICE)) == [("invoice", (1, 2))]


def test_untitled_pages_join_a_neighbour_and_untitled_files_keep_the_zone() -> None:
    pages = [
        _info(1, PageType.CONTINUATION),
        _info(2, PageType.GRN),
        _info(3, PageType.CONTINUATION),
    ]
    untitled = [_info(1, PageType.CONTINUATION), _info(2, PageType.EWAY_BILL)]

    assert _groups(group_pages(pages, DocType.INVOICE)) == [("grn", (1, 2, 3))]
    assert _groups(group_pages(untitled, DocType.INVOICE)) == [("invoice", (1, 2))]
    assert group_pages([], DocType.INVOICE) == ()
    assert not is_plain_split(group_pages(pages, DocType.INVOICE), DocType.INVOICE)


# --- pipeline --------------------------------------------------------------------------------


def _po_page(number: int) -> PageLayout:
    rows = [
        _row(100, (900, "PURCHASE ORDER")),
        _row(200, (100, "PO No.: SYN-PO-0007")),
        _row(250, (100, "Date: 01/08/2026")),
        _row(300, (100, f"GSTIN: {VENDOR_GSTIN}")),
    ]
    return _page(number, rows, TEXT)


class _Engine:
    def __init__(self, layout: DocumentLayout) -> None:
        self._layout = layout

    def layout(self, content: bytes, *, max_pages: int = 20) -> DocumentLayout:
        del content, max_pages
        return self._layout


def _parse(monkeypatch: pytest.MonkeyPatch, layout: DocumentLayout, split: PageSplit):
    monkeypatch.setattr(pipeline, "engine_for", lambda name, languages=("eng",): _Engine(layout))
    hints = ParserHints(doc_type=DocType.INVOICE, page_split=split)
    return pipeline.layout_parse(b"%PDF-synthetic", hints)


def _bundle() -> DocumentLayout:
    invoice = _invoice().pages[0]
    return DocumentLayout((_po_page(1), PageLayout(2, *_size(invoice), invoice.words, OCR)), 2)


def _size(page: PageLayout) -> tuple[int, int]:
    return page.width, page.height


def test_bundle_becomes_separate_documents_waiting_for_a_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _parse(monkeypatch, _bundle(), PageSplit.AUTO)

    assert result.success
    assert [document.header.doc_type for document in result.documents] == [
        DocType.PO,
        DocType.INVOICE,
    ]
    po, invoice = result.documents
    assert split_pages(po) == (1,) and split_pages(invoice) == (2,)
    assert po.source_locator == "layout:p1-1" and invoice.source_locator == "layout:p2-2"
    assert all(split_unconfirmed(document) for document in result.documents)
    assert all(document.needs_verification for document in result.documents)
    assert invoice.header.doc_no is not None and invoice.header.doc_no.value == "SYN-INV-0042"
    suggested = [w for w in result.warnings if w.code == "PAGE_SPLIT_SUGGESTED"]
    assert len(suggested) == 1 and "PO p1, INVOICE p2" in suggested[0].message


def test_accepted_split_is_confirmed_and_kept_file_is_one_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted = _parse(monkeypatch, _bundle(), PageSplit.CONFIRMED)
    kept = _parse(monkeypatch, _bundle(), PageSplit.OFF)

    assert len(accepted.documents) == 2
    assert not any(split_unconfirmed(document) for document in accepted.documents)
    assert "PAGE_SPLIT_SUGGESTED" not in {warning.code for warning in accepted.warnings}
    assert len(kept.documents) == 1
    assert kept.documents[0].header.doc_type is DocType.INVOICE
    assert "page_split" not in kept.documents[0].header.extras


def test_single_document_of_the_zone_type_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _parse(monkeypatch, _invoice(), PageSplit.AUTO)
    plain = _parse(monkeypatch, _invoice(), PageSplit.OFF)

    assert result == plain
    assert result.documents[0].source_locator == "layout"
