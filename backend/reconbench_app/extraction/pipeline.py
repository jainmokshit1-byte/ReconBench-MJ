"""Layout extraction inside the PDF sandbox: engine → words → document → dual-read.

Dual-read: document numbers, dates and PO references cannot be checked by arithmetic. For OCR
pages they are read a second time from a 2× crop in single-line mode; the field
is confirmed only when both readings agree exactly and contain no letter/digit
look-alikes (O/0, I/1). Disagreement leaves the field for human review.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from typing import Any

from reconbench_core.extraction.extract import (
    VERIFIED_CONFIDENCE,
    Extraction,
    extract_document,
    page_document_number,
)
from reconbench_core.extraction.layout import DocumentLayout, PageSource, full_rows
from reconbench_core.extraction.pages import PageGroup, classify_page, group_pages, is_plain_split
from reconbench_core.extraction.values import has_lookalike
from reconbench_core.parsers.parties import guard_result, guard_unconfigured_parties
from reconbench_core.parsers.types import (
    PageSplit,
    PageText,
    ParseResult,
    ParserHints,
    ParseWarning,
)
from reconbench_core.schema import DocType, Document, FileFormat, ReasonCode

from reconbench_app.extraction.engine import LayoutEngine, engine_for

_DUAL_READ_FIELDS = ("doc_no", "doc_date", "po_refs")
# A PO reference read beside a bare "Order No." label is capped below the OCR
# level: the label, not the reading, is in doubt, so a second reading cannot help.
_DUAL_READ_FLOOR = Decimal("0.65")


def _signature(extraction: Extraction) -> str:
    header = extraction.document.header
    vendor = header.vendor_gstin.value if header.vendor_gstin else "-"
    return hashlib.sha256(f"layout:{vendor}".encode()).hexdigest()


def _page_texts(layout: DocumentLayout) -> tuple[PageText, ...]:
    return tuple(
        PageText(
            page=page.number,
            content="\n".join(row.text for row in full_rows(page)),
            scanned=page.source == PageSource.OCR,
            is_ocr=page.source == PageSource.OCR,
        )
        for page in layout.pages
    )


def _dual_read(engine: LayoutEngine, document: Document) -> tuple[Document, list[str]]:
    reread = getattr(engine, "reread", None)
    if reread is None:
        return document, []
    confirmed: list[str] = []
    updates: dict[str, Any] = {}
    for name in _DUAL_READ_FIELDS:
        field = getattr(document.header, name, None)
        if field is None or not _DUAL_READ_FLOOR <= field.confidence < VERIFIED_CONFIDENCE:
            continue
        evidence = field.evidence
        if evidence.page is None or evidence.bbox is None:
            continue
        bbox = tuple(int(value) for value in evidence.bbox)
        second = reread(evidence.page, bbox)
        first = evidence.snippet or ""
        if not second or has_lookalike(first):
            continue
        if re.sub(r"\s+", "", second).strip(":.;,") == re.sub(r"\s+", "", first).strip(":.;,"):
            updates[name] = field.model_copy(update={"confidence": VERIFIED_CONFIDENCE})
            confirmed.append(name)
    if not updates:
        return document, []
    header = document.header.model_copy(update=updates)
    listed = list(header.extras.get("review_fields", []))
    review = [item for item in listed if item not in confirmed]
    header = header.model_copy(update={"extras": {**header.extras, "review_fields": review}})
    # Only clear the flag when the confirmed fields were its sole listed reasons
    # and nothing other than low confidence put the document in review.
    other_reasons = any(code != ReasonCode.LOW_CONFIDENCE_DATA for code in document.reason_codes)
    needs = bool(review) or other_reasons or (document.needs_verification and not listed)
    return (
        document.model_copy(
            update={
                "header": header,
                "needs_verification": needs,
                "reason_codes": document.reason_codes if needs else (),
            }
        ),
        confirmed,
    )


def _page_groups(
    layout: DocumentLayout, hints: ParserHints, zone: DocType
) -> tuple[PageGroup, ...]:
    if hints.page_split is PageSplit.OFF:
        return (PageGroup(zone, tuple(page.number for page in layout.pages)),)
    pages = {page.number: page for page in layout.pages}
    infos = [classify_page(page, full_rows(page)) for page in layout.pages]
    return group_pages(
        infos, zone, lambda number, doc_type: page_document_number(pages[number], doc_type)
    )


def _split_marker(
    document: Document, group: PageGroup, index: int, count: int, state: PageSplit
) -> Document:
    """Record the document's pages and whether a person confirmed the split."""

    locator = f"layout:p{group.pages[0]}-{group.pages[-1]}"
    marker = {
        "pages": list(group.pages),
        "group": index,
        "groups": count,
        "state": "confirmed" if state is PageSplit.CONFIRMED else "unconfirmed",
    }
    header = document.header.model_copy(
        update={"extras": {**document.header.extras, "page_split": marker}}
    )
    update: dict[str, Any] = {"header": header, "source_locator": locator}
    if state is not PageSplit.CONFIRMED:
        update["needs_verification"] = True
    return document.model_copy(update=update)


def layout_parse(
    content: bytes,
    hints: ParserHints,
    *,
    languages: tuple[str, ...] = ("eng",),
    engine_name: str = "local_hybrid",
    max_pages: int = 20,
) -> ParseResult:
    engine = engine_for(engine_name, languages=languages)
    layout = engine.layout(content, max_pages=max_pages)
    if hints.doc_type is None:
        return ParseResult(success=False, layout_signature="", reason_code="DOC_TYPE_REQUIRED")
    groups = _page_groups(layout, hints, hints.doc_type)
    if is_plain_split(groups, hints.doc_type):
        groups = (PageGroup(hints.doc_type, tuple(page.number for page in layout.pages)),)
        split = False
    else:
        split = True
    documents: list[Document] = []
    warnings: list[ParseWarning] = []
    confirmed: list[str] = []
    first: Extraction | None = None
    for index, group in enumerate(groups, start=1):
        pages = tuple(page for page in layout.pages if page.number in group.pages)
        extraction = extract_document(
            DocumentLayout(pages=pages, page_count=layout.page_count) if split else layout,
            doc_type=group.doc_type,
            charge_keywords=hints.charge_keywords,
            uom_aliases=dict(hints.uom_aliases),
            buyer_entities=hints.buyer_entities,
            gst_rates=hints.gst_rates,
        )
        if extraction is None:
            continue
        first = first or extraction
        document, read_twice = _dual_read(engine, extraction.document)
        confirmed.extend(name for name in read_twice if name not in confirmed)
        if not split:
            documents.append(document)
            warnings.extend(extraction.warnings)
            continue
        document = _split_marker(document, group, index, len(groups), hints.page_split)
        documents.append(document)
        warnings.extend(
            warning.model_copy(update={"locator": document.source_locator})
            for warning in extraction.warnings
        )
    if first is None:
        return ParseResult(
            success=False,
            layout_signature="",
            reason_code="NO_DOCUMENT_PAGES",
            page_texts=_page_texts(layout),
        )
    if split and hints.page_split is not PageSplit.CONFIRMED:
        described = ", ".join(
            f"{group.doc_type.value.upper()} p{group.pages[0]}"
            + (f"-{group.pages[-1]}" if len(group.pages) > 1 else "")
            for group in groups
        )
        warnings.append(
            ParseWarning(
                code="PAGE_SPLIT_SUGGESTED",
                message=(
                    f"This file looks like {len(documents)} document(s): {described}. "
                    "Accept the split or keep the file as one document."
                ),
            )
        )
    if any(page.source == PageSource.OCR for page in layout.pages):
        warnings.append(
            ParseWarning(
                code="OCR_LAYOUT_EXTRACTION",
                message=(
                    "Scanned pages were read by OCR. Values confirmed by arithmetic, checksum "
                    "or a second reading are marked verified; the rest need review."
                ),
            )
        )
    if any(document.needs_verification for document in documents) and any(
        page.source == PageSource.OCR for page in layout.pages
    ):
        warnings.append(
            ParseWarning(
                code="OCR_REVIEW_REQUIRED",
                message="OCR-read values that no cross-check confirmed need review.",
            )
        )
    if confirmed:
        warnings.append(
            ParseWarning(
                code="DUAL_READ_CONFIRMED",
                message="Confirmed by a second OCR reading: " + ", ".join(confirmed) + ".",
            )
        )
    result = ParseResult(
        success=True,
        file_format=FileFormat.PDF,
        documents=tuple(documents),
        warnings=tuple(warnings),
        layout_signature=_signature(first),
        page_texts=_page_texts(layout),
    )
    return guard_unconfigured_parties(guard_result(result, hints), hints)


def has_content(result: ParseResult) -> bool:
    """At least one critical header value or one line was read."""

    for document in result.documents:
        header = document.header
        if document.lines or any(
            getattr(header, name, None) is not None
            for name in ("doc_no", "doc_date", "grand_total", "vendor_gstin")
        ):
            return True
    return False


def verification_score(result: ParseResult) -> int:
    """Higher is better: present and confident critical fields, verified lines, clean QA."""

    if not result.success or not result.documents:
        return -1_000_000
    score = 0
    floor = Decimal("0.70")
    for document in result.documents:
        header = document.header
        for name in ("doc_no", "doc_date", "vendor_gstin", "taxable_total", "grand_total"):
            field = getattr(header, name, None)
            if field is not None:
                score += 1 + int(field.confidence >= Decimal("0.85"))
        for line in document.lines:
            if line.amount is not None:
                score += 2
                if all(
                    getattr(line, name) is None or getattr(line, name).confidence >= floor
                    for name in ("quantity", "rate", "amount")
                ):
                    score += 1
        if not document.needs_verification:
            score += 10
    if any(warning.code == "EXTRACTION_QA_FAILED" for warning in result.warnings):
        score -= 5
    return score


__all__ = ["has_content", "layout_parse", "verification_score"]
