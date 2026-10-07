"""Which PDF documents may receive H1 extraction suggestions.

Invoices reuse the deterministic critical-field gate (``needs_verification``).
PO and GRN rules are the *proposed* defaults from decision D13, awaiting Product
approval; they are verdict-neutral because suggestions never apply themselves.
In ``ai_first`` mode every PDF with usable text is eligible (decision D12).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

PO_GRN_RULES_STATUS = "proposed, awaiting Product approval (D13)"
CRITICAL_CONFIDENCE_FLOOR = Decimal("0.70")
MIN_USABLE_TEXT_CHARS = 40
PDF_METHODS = frozenset({"pdf_text", "pdf_ocr"})

SkipReason = Literal[
    "NOT_PDF",
    "NO_USABLE_TEXT",
    "TOO_MANY_PAGES",
    "TOO_MUCH_TEXT",
    "NOT_NEEDED",
    "MODE_OFF",
]


@dataclass(frozen=True, slots=True)
class H1Facts:
    doc_type: Literal["invoice", "po", "grn"]
    file_format: str
    extraction_method: str | None
    needs_verification: bool
    has_doc_no: bool
    has_doc_date: bool
    has_vendor: bool
    has_usable_lines: bool
    min_critical_confidence: Decimal | None
    page_count: int
    text_chars: int
    text_bytes: int


@dataclass(frozen=True, slots=True)
class H1Eligibility:
    eligible: bool
    reason: SkipReason | None = None


def h1_eligibility(
    facts: H1Facts, *, mode: str, max_pages: int, max_input_bytes: int
) -> H1Eligibility:
    if mode not in {"ai_assisted", "ai_first"}:
        return H1Eligibility(False, "MODE_OFF")
    if facts.file_format != "pdf" or facts.extraction_method not in PDF_METHODS:
        return H1Eligibility(False, "NOT_PDF")
    if facts.text_chars < MIN_USABLE_TEXT_CHARS:
        return H1Eligibility(False, "NO_USABLE_TEXT")
    if facts.page_count > max_pages:
        return H1Eligibility(False, "TOO_MANY_PAGES")
    if facts.text_bytes > max_input_bytes:
        return H1Eligibility(False, "TOO_MUCH_TEXT")
    if mode == "ai_first":
        return H1Eligibility(True)
    if facts.doc_type == "invoice":
        return (
            H1Eligibility(True) if facts.needs_verification else H1Eligibility(False, "NOT_NEEDED")
        )
    missing = not (
        facts.has_doc_no and facts.has_doc_date and facts.has_vendor and facts.has_usable_lines
    )
    low_confidence = (
        facts.min_critical_confidence is not None
        and facts.min_critical_confidence < CRITICAL_CONFIDENCE_FLOOR
    )
    return H1Eligibility(True) if missing or low_confidence else H1Eligibility(False, "NOT_NEEDED")


__all__ = [
    "CRITICAL_CONFIDENCE_FLOOR",
    "MIN_USABLE_TEXT_CHARS",
    "PO_GRN_RULES_STATUS",
    "H1Eligibility",
    "H1Facts",
    "h1_eligibility",
]
