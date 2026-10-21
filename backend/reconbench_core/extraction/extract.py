"""Assemble one document from positioned words: classify, read, cross-check, score.

The result goes through the shared ``build_document`` path, so typing, GSTIN and
arithmetic QA stay identical for every parser. Confidence then reflects the
cross-check outcome rather than OCR certainty alone:

* verified by arithmetic, checksum or agreement between pages → 0.98;
* read from a digital text layer but not cross-checked → 0.90;
* read by OCR but not cross-checked → 0.65 (below the 0.70 review gate);
* obtained only by division → 0.60.

So a scanned document passes without review only when its numbers agree.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from reconbench_core.extraction.header import (
    Candidate,
    aligned_below,
    find_labels,
    norm,
    values_for,
)
from reconbench_core.extraction.layout import (
    DocumentLayout,
    PageLayout,
    PageSource,
    TextLine,
    Word,
    full_rows,
    typical_height,
)
from reconbench_core.extraction.pages import is_eway_bill
from reconbench_core.extraction.table import Col, Item, TableResult, extract_tables, norm_spaces
from reconbench_core.extraction.values import (
    amount_in_words,
    find_dates,
    gstins_in,
    has_lookalike,
    read_gstin,
    read_number,
)
from reconbench_core.extraction.verify import (
    DocumentNumbers,
    LineNumbers,
    Num,
    Status,
    solve_document,
    solve_line,
)
from reconbench_core.parsers.common import build_document
from reconbench_core.parsers.parties import (
    buyer_gstins,
    is_buyer_name,
    name_problem,
    withhold_vendor_name,
)
from reconbench_core.parsers.types import DEFAULT_GST_RATES, BuyerEntityHint, ParseWarning
from reconbench_core.schema import (
    DocType,
    Document,
    FieldEvidence,
    FieldOrigin,
    FieldSource,
    LineType,
    ReasonCode,
)

VERIFIED_CONFIDENCE = Decimal("0.98")
TEXT_LAYER_CONFIDENCE = Decimal("0.90")
OCR_CONFIDENCE = Decimal("0.65")
DERIVED_CONFIDENCE = Decimal("0.60")
CLEAR_CONFIDENCE = Decimal("0.85")
REVIEW_FLOOR = Decimal("0.70")


class PageKind(StrEnum):
    DOCUMENT = "document"
    EWAY_BILL = "eway_bill"


@dataclass(frozen=True, slots=True)
class FieldCheck:
    status: Status
    note: str = ""
    # Highest confidence the field may get, whatever its status (weak labels).
    cap: Decimal | None = None


@dataclass
class Extraction:
    document: Document
    warnings: tuple[ParseWarning, ...]
    checks: dict[str, FieldCheck] = field(default_factory=dict)
    line_checks: list[dict[str, FieldCheck]] = field(default_factory=list)
    page_kinds: dict[int, PageKind] = field(default_factory=dict)


_INT_PART = re.compile(r"^[\d,]+$")
_FRACTION = re.compile(r"^\.\d{1,2}$")


def merge_fragments(page: PageLayout) -> PageLayout:
    """Join numbers OCR split at the decimal point: "271255" + ".97" -> "271255.97"."""

    words = sorted(page.words, key=lambda word: (word.cy, word.x0))
    merged: list[Word] = []
    for word in words:
        if merged:
            last = merged[-1]
            if (
                _INT_PART.match(last.text)
                and _FRACTION.match(word.text)
                and abs(last.cy - word.cy) <= max(4, last.height // 2)
                and 0 <= word.x0 - last.x1 <= max(12, last.height)
            ):
                merged[-1] = Word(
                    last.text + word.text,
                    last.x0,
                    min(last.y0, word.y0),
                    word.x1,
                    max(last.y1, word.y1),
                    min(last.conf, word.conf),
                )
                continue
        merged.append(word)
    return replace(page, words=tuple(merged))


def classify_page(page: PageLayout, rows: Sequence[TextLine]) -> PageKind:
    return PageKind.EWAY_BILL if is_eway_bill(page) else PageKind.DOCUMENT


_DOC_NO_LABELS = {
    DocType.INVOICE: ("INVOICE NUMBER", "INVOICE NO", "INV NO", "BILL NO", "TAX INVOICE NO"),
    # Tally prints "Voucher No." for its own number on purchase orders and receipt notes.
    DocType.PO: (
        "PURCHASE ORDER NO",
        "PURCHASE ORDER NUMBER",
        "PO NUMBER",
        "PO NO",
        "VOUCHER NO",
        "ORDER NO",
    ),
    DocType.GRN: (
        "RECEIPT NOTE NO",
        "GRN NUMBER",
        "GRN NO",
        "MRN NO",
        "RECEIPT NO",
        "VOUCHER NO",
    ),
}
_DATE_LABELS = (
    "INVOICE DATE",
    "INVOICE DATE TIME",
    "INVOICE DATEITIME",
    "DATED",
    "DATE",
    "BILL DATE",
)
_DATE_EXCLUDE = frozenset(
    {
        "DUE",
        "ORDER",
        "ACK",
        "DELIVERY",
        "START",
        "END",
        "PO",
        "DESPATCH",
        "DISPATCH",
        "GENERATED",
        "RECEIPT",
        "CHALLAN",
        "NOTE",
        "EWAY",
        "ENTERED",
        "VALID",
        "AND",
        "TRANSPORTER",
    }
)
_PO_LABELS = {
    DocType.INVOICE: (
        "CUSTOMER PO NUMBER",
        "CUSTOMER PO NO",
        "CUST PO",
        "BUYERS ORDER NO",
        "BUYER S ORDER NO",
        "PO NO",
        "PO NUMBER",
        "PURCHASE ORDER NO",
        "CUST ORDER NO",
    ),
    # A Tally receipt note prints the purchase order as "Reference No. & Date".
    DocType.GRN: (
        "PO NO",
        "PO NUMBER",
        "PURCHASE ORDER NO",
        "AGAINST PO",
        "REFERENCE NO",
        "ORDER NO",
    ),
}
# A bare "Order No." can be the buyer's PO, the seller's sales order or a
# marketplace order. It is read only when no PO label gave a value, never
# straight after a seller-side word, and always left for a person to confirm.
_GENERIC_ORDER_LABELS = ("ORDER NO", "ORDER NUMBER")
_SELLER_ORDER_WORDS = frozenset({"SALES", "SALE", "SO", "OUR", "SELLER", "DELIVERY"})
# Marketplace order numbers (e.g. 407-0121734-2276374) are not purchase orders.
_MARKETPLACE_ORDER = re.compile(r"\d{3}-\d{7}-\d{7}|OD\d{15,21}")
_BUYER_MARKERS = re.compile(
    r"\b(BILL TO|BILLED TO|BUYER|CONSIGNEE|SHIP TO|SHIPPED TO|RECIPIENT|BILLING ADDRESS|"
    r"SHIPPING ADDRESS|CUSTOMER|TO)\b"
)
_SELLER_MARKERS = re.compile(
    r"\b(SOLD BY|SUPPLIER|SELLER|GST REG NO|GST REGISTRATION|FROM|TRADE|LEGAL NAME|SHIPPED FROM)\b"
)
_DOC_NO_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_.\-]*[A-Za-z0-9]|\d")
_FOLD = str.maketrans(
    {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}
)


def _fold(value: str) -> str:
    return norm(value).translate(_FOLD)


def accept_doc_no(text: str) -> str | None:
    if text.rstrip()[-1:] in {"-", "/"}:
        # "SERVICEPUR/26-": the number continues in the next word.
        return None
    value = text.strip(" :.-|#;,")
    parts = value.split(" ")
    if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) <= 2 and parts[1].isalnum():
        # "1 610357353": OCR split the first digit off a long number.
        value = parts[0] + parts[1]
    elif len(parts) == 2 and all(parts) and (parts[0][-1] in "-/" or parts[1][0] in "-/"):
        # "SERVICEPUR/26- 27/08-42": a number wrapped or split at its separator.
        value = parts[0] + parts[1]
    if not 1 <= len(value) <= 30 or not any(char.isdigit() for char in value):
        return None
    if not _DOC_NO_SHAPE.fullmatch(value) or read_gstin(value) is not None:
        return None
    if re.fullmatch(r"[\d,]+\.\d{1,2}", value):
        return None
    dates = find_dates(value)
    if dates and len(dates[0].raw) >= len(value) - 1:
        return None
    return value


def accept_date(text: str) -> date | None:
    dates = find_dates(text)
    return dates[0].value if dates else None


def accept_money(text: str, *, allow_repaired: bool = False) -> Decimal | None:
    if "%" in text:
        return None
    reading = read_number(text)
    if reading is None or (reading.repaired and not allow_repaired):
        return None
    if "." not in text and "," not in text:
        return None
    return reading.value


def accept_po(text: str) -> str | None:
    """A PO reference shaped like a document number (not an address number or a date)."""

    value = text.strip(" :.-|#;,_")
    if len(value) < 3 or not any(char.isdigit() for char in value) or value.upper() == "MAIL":
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.\-]*", value):
        return None
    # A bare short number ("501") is a flat, plot or row number, not a PO.
    if value.isdigit() and len(value) < 5:
        return None
    if _MARKETPLACE_ORDER.fullmatch(value):
        return None
    if find_dates(value) and len(find_dates(value)[0].raw) >= len(value) - 1:
        return None
    return value


@dataclass
class _Context:
    pages: list[PageLayout]
    rows: dict[int, tuple[TextLine, ...]]
    height: int

    @property
    def all_rows(self) -> list[TextLine]:
        return [row for page in self.pages for row in self.rows[page.number]]


CLEAR_CONF = 93


def _clear(candidate: Candidate) -> bool:
    """Every character read with high confidence and no letter/digit look-alike in a mix."""

    return candidate.conf >= CLEAR_CONF and not has_lookalike(str(candidate.raw))


_EWAY_DOC = re.compile(
    r"(?:DOCUMENT\s*NO\.?\s*:?|TAX\s+INVOICE\s*-)\s*([A-Za-z0-9][A-Za-z0-9/_.\-]{2,29})", re.I
)


def _eway_document_numbers(rows: Sequence[TextLine]) -> list[Candidate]:
    """Document numbers printed on an e-way bill ("Tax Invoice - X - date", "Document No : X")."""

    found: list[Candidate] = []
    for index, row in enumerate(rows):
        joined = row.text + " " + (rows[index + 1].text if index + 1 < len(rows) else "")
        for match in _EWAY_DOC.finditer(joined):
            value = accept_doc_no(match.group(1))
            if value is None:
                continue
            words = (
                tuple(w for r in rows[index : index + 2] for w in r.words if value in w.text)
                or row.words
            )
            found.append(Candidate(value, value, row.page, words, "EWAY", 0))
    return found


def _doc_no(
    ctx: _Context, eway: list[_Context], doc_type: DocType
) -> tuple[Candidate | None, FieldCheck, list[str]]:
    candidates: list[Candidate] = []
    labels = [label for label in _DOC_NO_LABELS[doc_type] if "ORDER" not in label.split()]
    order_labels = [label for label in _DOC_NO_LABELS[doc_type] if "ORDER" in label.split()]
    exclude = frozenset({"REFERENCE", "EWAY"})
    # "Order No." is a PO's own number only when no seller or buyer word qualifies it.
    order_exclude = exclude | _SELLER_ORDER_WORDS | {"BUYERS", "BUYER", "S", "CUST", "CUSTOMER"}
    for page in ctx.pages:
        rows = ctx.rows[page.number]
        hits = find_labels(rows, labels, exclude_before=exclude) + find_labels(
            rows, order_labels, exclude_before=order_exclude
        )
        hits = [hit for hit in hits if "REFERENCE" not in norm(hit.line.text[:80])]
        found = values_for(hits, rows, accept_doc_no, height=ctx.height, max_join=2)
        if found:
            candidates.append(found[0])
    eway_values: list[Candidate] = []
    for other in eway:
        for page in other.pages:
            eway_values.extend(_eway_document_numbers(other.rows[page.number]))
    notes: list[str] = []
    if not candidates and not eway_values:
        return None, FieldCheck(Status.MISSING), notes
    groups: Counter[str] = Counter(_fold(candidate.value) for candidate in candidates)
    best_fold = groups.most_common(1)[0][0] if groups else _fold(eway_values[0].value)
    matching_eway = [c for c in eway_values if _fold(c.value) == best_fold]
    if eway_values and not matching_eway:
        notes.append("ATTACHED_EWAY_BILL_OTHER_DOCUMENT")
    group = [c for c in candidates if _fold(c.value) == best_fold]
    if matching_eway:
        chosen = matching_eway[0]
        if has_lookalike(chosen.value):
            # Agreement cannot settle letter/digit look-alikes (O vs 0): both copies
            # may be misread the same way, and canonical matching keeps them distinct.
            return chosen, FieldCheck(Status.UNVERIFIED, "ambiguous characters"), notes
        return chosen, FieldCheck(Status.CONFIRMED, "matches e-way bill document number"), notes
    variants = Counter(c.value for c in group)
    chosen = max(group, key=lambda c: (variants[c.value], c.conf))
    pages = {c.page for c in group}
    if len(pages) >= 2 and variants[chosen.value] >= 2 and not has_lookalike(chosen.value):
        return chosen, FieldCheck(Status.CONFIRMED, "same number on several pages"), notes
    if _clear(chosen):
        return chosen, FieldCheck(Status.CLEAR, "clear reading"), notes
    return chosen, FieldCheck(Status.UNVERIFIED), notes


def page_document_number(page: PageLayout, doc_type: DocType) -> str | None:
    """One page's own document number, look-alike folded, for page grouping."""

    merged = merge_fragments(page)
    ctx = _Context([merged], {merged.number: full_rows(merged)}, typical_height(merged.words))
    found, _check, _notes = _doc_no(ctx, [], doc_type)
    return None if found is None else _fold(found.value)


def _first(
    ctx: _Context,
    labels: Sequence[str],
    accept: Any,
    *,
    exclude: frozenset[str] = frozenset(),
    max_join: int = 1,
) -> Candidate | None:
    for page in ctx.pages:
        rows = ctx.rows[page.number]
        hits = find_labels(rows, labels, exclude_before=exclude)
        ranked = sorted(hits, key=lambda hit: labels.index(hit.label))
        for hit in ranked:
            found = values_for([hit], rows, accept, height=ctx.height, max_join=max_join)
            if found:
                return found[0]
    return None


_BUYER_WORDS = re.compile(
    r"\b(BILL TO|BILLED TO|BUYER|CONSIGNEE|SHIP TO|SHIPPED TO|BILLING ADDRESS|SHIPPING ADDRESS)\b"
)
_SELLER_WORDS = re.compile(
    r"\b(SOLD BY|SUPPLIER|SELLER|GST REG NO|GST REGISTRATION|SHIPPED FROM|TRADE|LEGAL NAME)\b"
)


def _role(rows: Sequence[TextLine], index: int, x0: int, x1: int) -> str:
    """Nearest buyer/seller label on the GSTIN's own row, else in the block above it."""

    own = norm_spaces(rows[index].text)
    if _BUYER_WORDS.search(own) or own.startswith("TO "):
        return "buyer"
    if _SELLER_WORDS.search(own):
        return "seller"
    for above in reversed(rows[max(0, index - 12) : index]):
        near = [w for w in above.words if w.x1 >= x0 - 400 and w.x0 <= x1]
        text = norm_spaces(" ".join(w.text for w in near))
        if not text:
            continue
        if _BUYER_WORDS.search(text) or text == "TO" or text.startswith("TO "):
            return "buyer"
        if _SELLER_WORDS.search(text):
            return "seller"
    return "unknown"


def _gstins(
    ctx: _Context, own: frozenset[str] = frozenset()
) -> tuple[tuple[str, Candidate] | None, tuple[str, Candidate] | None, FieldCheck]:
    """Vendor and buyer GSTINs.

    A GSTIN of one of the buyer's own entities (``own``) is always the buyer and
    never the vendor. Otherwise the buyer's GSTIN is usually printed in both the
    bill-to and ship-to blocks, so a GSTIN that appears twice on one page is the
    buyer; labels decide the rest.

    Without ``own`` (the workspace has not configured its GSTINs) a checksum alone
    never confirms a vendor whose role no label establishes: one unlabelled GSTIN,
    or an unlabelled vendor beside another unlabelled GSTIN, may be the buyer's
    own, so it is capped below the review floor. A labelled buyer GSTIN beside it
    establishes the vendor's role by elimination.
    """

    occurrences: list[tuple[str, bool, Candidate, str]] = []
    per_page: Counter[tuple[int, str]] = Counter()
    for page in ctx.pages:
        rows = ctx.rows[page.number]
        for index, row in enumerate(rows):
            for reading in gstins_in(row.text):
                words = (
                    tuple(
                        w
                        for w in row.words
                        if len(norm(w.text)) >= 4 and norm(w.text) in norm(reading.raw)
                    )
                    or row.words
                )
                x0 = min(w.x0 for w in words)
                x1 = max(w.x1 for w in words)
                candidate = Candidate(
                    reading.value,
                    reading.raw,
                    page.number,
                    words,
                    "GSTIN",
                    0,
                    repaired=reading.repaired,
                )
                occurrences.append(
                    (reading.value, reading.repaired, candidate, _role(rows, index, x0, x1))
                )
                per_page[(page.number, reading.value)] += 1
    if not occurrences:
        return None, None, FieldCheck(Status.MISSING)
    values = list(dict.fromkeys(value for value, *_rest in occurrences))

    def buyer_score(value: str) -> int:
        repeats = max(count for (_page, v), count in per_page.items() if v == value)
        roles = Counter(role for v, _r, _c, role in occurrences if v == value)
        return (2 if repeats >= 2 else 0) + 2 * roles["buyer"] - 2 * roles["seller"]

    buyer_value = None
    own_values = [value for value in values if value in own]
    if own_values:
        buyer_value = max(own_values, key=buyer_score)
    elif len(values) >= 2:
        ranked = sorted(values, key=buyer_score, reverse=True)
        if buyer_score(ranked[0]) > buyer_score(ranked[1]):
            buyer_value = ranked[0]
    elif buyer_score(values[0]) >= 2:
        buyer_value = values[0]
    others = [value for value in values if value != buyer_value and value not in own]
    vendor_value = min(others, key=buyer_score) if others else None

    def pick(value: str | None) -> tuple[str, Candidate] | None:
        if value is None:
            return None
        cands = [c for v, _r, c, _x in occurrences if v == value]
        return value, min(cands, key=lambda c: (c.repaired, -c.conf))

    vendor = pick(vendor_value)
    check = FieldCheck(Status.MISSING)
    if vendor is not None:
        unrepaired = [c for v, r, c, _x in occurrences if v == vendor[0] and not r]
        pan_seen = any(
            vendor[0][2:12] == norm(word.text)
            for page in ctx.pages
            for row in ctx.rows[page.number]
            for word in row.words
        )
        repeated = sum(1 for v, *_r in occurrences if v == vendor[0]) >= 2

        def role_of(value: str) -> str:
            roles = {role for v, _r, _c, role in occurrences if v == value}
            return next((role for role in ("seller", "buyer") if role in roles), "unknown")

        vendor_role = role_of(vendor[0])
        unlabelled_other = any(
            value != vendor[0] and role_of(value) == "unknown" for value in values
        )
        if not own and vendor_role == "unknown" and (len(values) == 1 or unlabelled_other):
            check = FieldCheck(
                Status.UNVERIFIED, "party role not established", cap=DERIVED_CONFIDENCE
            )
        elif unrepaired or pan_seen or repeated:
            check = FieldCheck(Status.CONFIRMED, "GSTIN checksum valid")
        else:
            check = FieldCheck(Status.UNVERIFIED, "GSTIN repaired from OCR")
    return vendor, pick(buyer_value), check


def _row_amounts(row: TextLine) -> list[Decimal]:
    values = []
    for word in row.words:
        value = accept_money(word.text)
        if value is not None:
            values.append(value)
    return values


def _summary_candidates(
    ctx: _Context, tables: TableResult
) -> dict[str, list[tuple[Decimal, Candidate | None]]]:
    found: dict[str, list[tuple[Decimal, Candidate | None]]] = {
        "grand": [],
        "taxable": [],
        "cgst": [],
        "sgst": [],
        "igst": [],
        "round_off": [],
    }
    label_sets = {
        "grand": (
            "GRAND TOTAL",
            "TOTAL VALUE",
            "TOTAL INV AMT",
            "INVOICE VALUE",
            "TOTAL AMOUNT",
            "NET PAYABLE",
            "AMOUNT PAYABLE",
            "TOTAL INVOICE VALUE",
            "INVOICE TOTAL",
            "TOTAL",
        ),
        "taxable": (
            "TAXABLE VALUE",
            "TAXABLE AMOUNT",
            "SUBTOTAL",
            "SUB TOTAL",
            "SUB TOTAL 1",
            "TOT TAXBLE AMT",
            "TOTAL TAXABLE VALUE",
            "TOTEL",
            "BASIC",
        ),
        "cgst": ("CGST", "TOTAL CGST", "CGST OUTPUT", "CENTRAL TAX"),
        "sgst": ("SGST", "TOTAL SGST", "SGST OUTPUT", "STATE TAX", "UTGST"),
        "igst": ("IGST", "TOTAL IGST", "INTEGRATED TAX"),
        "round_off": ("ROUND OFF", "ROUNDED OFF", "ROUNDING OFF"),
    }
    for page in ctx.pages:
        rows = ctx.rows[page.number]
        for name, labels in label_sets.items():
            for hit in find_labels(rows, labels):
                amounts = [
                    (amount, w)
                    for w in hit.line.words[hit.end :]
                    if (amount := accept_money(w.text, allow_repaired=True)) is not None
                ]
                if amounts:
                    value, word = amounts[-1]
                    found[name].append(
                        (value, Candidate(value, word.text, page.number, (word,), hit.label, 0))
                    )
                below = aligned_below(
                    hit,
                    rows,
                    lambda text: accept_money(text, allow_repaired=True),
                    height=ctx.height,
                )
                if below is not None:
                    found[name].append((below.value, below))
    for row in tables.summary_rows:
        if row.amount is None:
            continue
        upper = norm_spaces(row.text)
        if re.search(r"\bCGST\b", upper):
            found["cgst"].append((row.amount.value, None))
        elif re.search(r"\b(SGST|UTGST)\b", upper):
            found["sgst"].append((row.amount.value, None))
        elif re.search(r"\bIGST\b", upper):
            found["igst"].append((row.amount.value, None))
        else:
            found["taxable"].append((row.amount.value, None))
            found["grand"].append((row.amount.value, None))
    return found


def _words_totals(ctx: _Context) -> list[Decimal]:
    totals = []
    for page in ctx.pages:
        rows = ctx.rows[page.number]
        for index, row in enumerate(rows):
            words = list(row.words)
            if index + 1 < len(rows) and rows[index + 1].cy - row.cy <= ctx.height * 1.2:
                # Amounts in words often wrap or sit on a slanted baseline.
                words += list(rows[index + 1].words)
            words.sort(key=lambda word: word.x0)
            upper = norm_spaces(" ".join(word.text for word in words))
            if ("ONLY" in upper or "RUPEES" in upper or "PAISA" in upper or "PAISE" in upper) and (
                re.search(r"\b(THOUSAND|LAKH|HUNDRED|MILLION|CRORE)\b", upper)
            ):
                value = amount_in_words(upper)
                if value is not None:
                    totals.append(value)
    return totals


def _num(reading: Any) -> Num:
    return Num(reading.value if reading is not None else None)


def _line_numbers(item: Item) -> LineNumbers:
    taxes = [Num(r.value) for r in item.numbers(Col.TAX) if r.value > 0]
    rates = [r.value for r in item.numbers(Col.TAX_RATE) if Decimal("0") < r.value <= 28]
    if not rates:
        for word in item.values.get(Col.TAX_RATE, []) + item.values.get(Col.TAX, []):
            match = re.search(r"(\d{1,2}(?:\.\d{1,2})?)\s*%", word.text)
            if match:
                rates.append(Decimal(match.group(1)))
    tax_amounts = [Num(r.value) for r in item.numbers(Col.TAX_RATE) if r.value > 28]
    freight = item.first_number(Col.FREIGHT)
    return LineNumbers(
        quantity=_num(item.first_number(Col.QTY)),
        rate=_num(item.numbers(Col.RATE)[-1] if item.numbers(Col.RATE) else None),
        amount=_num(item.numbers(Col.AMOUNT)[-1] if item.numbers(Col.AMOUNT) else None),
        subtotal=_num(item.first_number(Col.SUBTOTAL)),
        discount=_num(item.first_number(Col.DISCOUNT)),
        freight=_num(freight),
        extended=_num(item.first_number(Col.TOTAL_INCL)),
        tax_rate=rates[0] if rates else None,
        taxes=taxes or tax_amounts,
    )


def _confidence(status: Status, source: PageSource) -> Decimal:
    if status in {Status.CONFIRMED, Status.CORRECTED}:
        return VERIFIED_CONFIDENCE
    if status == Status.CLEAR:
        return CLEAR_CONFIDENCE
    if status == Status.DERIVED:
        return DERIVED_CONFIDENCE
    return TEXT_LAYER_CONFIDENCE if source == PageSource.TEXT_LAYER else OCR_CONFIDENCE


def _evidence(candidate: Candidate | None) -> FieldEvidence | None:
    if candidate is None:
        return None
    x0, y0, x1, y1 = candidate.bbox
    return FieldEvidence(
        page=candidate.page,
        snippet=candidate.raw[:200],
        bbox=(Decimal(x0), Decimal(y0), Decimal(x1), Decimal(y1)),
    )


def _acceptable_name(name: str, buyers: Sequence[BuyerEntityHint]) -> bool:
    return (
        len(name.split()) >= 2
        and sum(char.isalpha() for char in name) >= 4
        and name_problem(name) is None
        and not is_buyer_name(name, buyers)
    )


def _signatory_name(ctx: _Context, buyers: Sequence[BuyerEntityHint]) -> str | None:
    """The "for <Company>" line above the signature; the buyer's own is skipped (PO, GRN)."""

    for page in reversed(ctx.pages):
        for row in reversed(ctx.rows[page.number]):
            confident = " ".join(w.text for w in row.words if w.conf >= 60)
            match = re.match(r"^\s*for\s+(.{4,80}?)[\s:.]*$", confident, re.I)
            if match:
                name = match.group(1).strip(" :.")
                if len(name.split()) >= 2 and _acceptable_name(name, buyers):
                    return name
    return None


# Labels of the supplier's block. The name is the text after the label on its
# row, or the first line under it in the same column.
_SUPPLIER_LABELS = (
    "SUPPLIER BILL FROM",
    "BILL FROM",
    "BILL SHIPPED FROM",
    "SHIPPED FROM",
    "SOLD BY",
    "BILLED BY",
    "SELLER",
    "SUPPLIER",
    "VENDOR",
)


def _column_text(words: Sequence[Word], height: int) -> str:
    """Words left to right up to the first wide gap (the next column)."""

    ordered = sorted(words, key=lambda word: word.x0)
    taken: list[Word] = []
    for word in ordered:
        if taken and word.x0 - taken[-1].x1 > 3 * height:
            break
        taken.append(word)
    tokens = [word.text for word in taken]
    # Specks and stray letters at either end ("Phoenyx Commtech Pvt Ltd F").
    while tokens and len(tokens[-1].strip(" :.-|,")) <= 1:
        tokens.pop()
    while tokens and len(tokens[0].strip(" :.-|,")) <= 1:
        tokens.pop(0)
    return " ".join(tokens).strip(" :.-|,")


def _supplier_block_name(ctx: _Context, buyers: Sequence[BuyerEntityHint]) -> str | None:
    for page in ctx.pages:
        rows = ctx.rows[page.number]
        hits = find_labels(rows, _SUPPLIER_LABELS)
        hits.sort(key=lambda hit: (_SUPPLIER_LABELS.index(hit.label), hit.cy))
        for hit in hits:
            height = ctx.height
            after = [
                word
                for word in hit.line.words[hit.end :]
                if word.x0 - hit.x1 <= 3 * height and norm(word.text)
            ]
            if after:
                name = _column_text(after, height)
                if _acceptable_name(name, buyers):
                    return name
            # Row centres, not word boxes: a speck can stretch the label's box.
            top = hit.cy + height // 2
            below = [
                row
                for row in rows
                if top < row.cy <= hit.cy + 5 * height
                and any(hit.x0 - 2 * height <= w.x0 <= hit.x0 + 8 * height for w in row.words)
            ]
            if not below:
                continue
            first = min(below, key=lambda row: row.cy)
            # A slanted scan splits one printed line into neighbouring rows.
            words = [
                word
                for row in rows
                if abs(row.cy - first.cy) <= height and row.cy > top
                for word in row.words
                if word.x0 >= hit.x0 - 2 * height
            ]
            name = _column_text(words, height)
            if _acceptable_name(name, buyers):
                return name
    return None


# Amounts printed with a rupee sign that OCR kept as "₹" or read as "%".
_RUPEE_TOKEN = re.compile(r"^-?[₹%]\s*\d")


def _rupee_glyph(pages: Sequence[PageLayout]) -> bool:
    """The page prints "₹" before amounts, so OCR may also read the sign as a digit."""

    return sum(1 for page in pages for word in page.words if _RUPEE_TOKEN.match(word.text)) >= 2


def extract_document(
    layout: DocumentLayout,
    *,
    doc_type: DocType,
    charge_keywords: tuple[str, ...] = (),
    uom_aliases: dict[str, str] | None = None,
    buyer_entities: Sequence[BuyerEntityHint] = (),
    gst_rates: Sequence[Decimal] = DEFAULT_GST_RATES,
) -> Extraction | None:
    pages = [merge_fragments(page) for page in layout.pages]
    rows = {page.number: full_rows(page) for page in pages}
    kinds = {page.number: classify_page(page, rows[page.number]) for page in pages}
    document_pages = [page for page in pages if kinds[page.number] == PageKind.DOCUMENT]
    if not document_pages:
        return None
    height = typical_height(word for page in document_pages for word in page.words)
    ctx = _Context(document_pages, rows, height)
    eway = [
        _Context([page], rows, typical_height(page.words))
        for page in pages
        if kinds[page.number] == PageKind.EWAY_BILL and page.words
    ]
    source = (
        PageSource.OCR
        if any(page.source == PageSource.OCR for page in document_pages)
        else PageSource.TEXT_LAYER
    )
    warnings: list[ParseWarning] = []
    checks: dict[str, FieldCheck] = {}
    evidence: dict[str, FieldEvidence | None] = {}
    header_raw: dict[str, str] = {}

    doc_no, doc_no_check, notes = _doc_no(ctx, eway, doc_type)
    for note in notes:
        warnings.append(
            ParseWarning(
                code=note,
                message="An attached e-way bill belongs to a different document and was ignored.",
            )
        )
    if doc_no is not None:
        header_raw["doc_no"] = doc_no.value
        checks["doc_no"] = doc_no_check
        evidence["doc_no"] = _evidence(doc_no)

    doc_date = _first(ctx, _DATE_LABELS, accept_date, exclude=_DATE_EXCLUDE, max_join=3)
    if doc_date is not None:
        header_raw["doc_date"] = doc_date.value.isoformat()
        date_repaired = any(reading.repaired for reading in find_dates(doc_date.raw))
        same = sum(
            1
            for page in ctx.pages
            for row in rows[page.number]
            if any(d.value == doc_date.value for d in find_dates(row.text))
        )
        if date_repaired:
            checks["doc_date"] = FieldCheck(Status.UNVERIFIED, "month name partly read")
        elif same >= 2:
            checks["doc_date"] = FieldCheck(Status.CONFIRMED, "same date printed more than once")
        elif _clear(doc_date):
            checks["doc_date"] = FieldCheck(Status.CLEAR, "clear reading")
        else:
            checks["doc_date"] = FieldCheck(Status.UNVERIFIED)
        evidence["doc_date"] = _evidence(doc_date)

    own = buyer_gstins(buyer_entities)
    vendor, buyer, vendor_check = _gstins(ctx, own)
    if vendor is not None:
        header_raw["vendor_gstin"] = vendor[0]
        checks["vendor_gstin"] = vendor_check
        evidence["vendor_gstin"] = _evidence(vendor[1])
    if buyer is not None:
        header_raw["buyer_gstin"] = buyer[0]
        checks["buyer_gstin"] = FieldCheck(Status.CONFIRMED, "GSTIN checksum valid")
        evidence["buyer_gstin"] = _evidence(buyer[1])
    if (
        vendor is None
        and buyer is None
        and any("GSTIN" in norm(row.text) or "GSTREG" in norm(row.text) for row in ctx.all_rows)
    ):
        warnings.append(
            ParseWarning(
                code="GSTIN_INVALID",
                message="A GSTIN is printed but could not be read with a valid checksum.",
            )
        )
    # A PO or GRN is issued by the buyer, so its "for <Company>" line is the buyer's own
    # name. Without configured entities nothing rejects it: only a name under a supplier
    # label is used, and an issuer line alone leaves the vendor name for review.
    issuer_is_buyer = doc_type in {DocType.PO, DocType.GRN} and not buyer_entities
    if issuer_is_buyer:
        name = _supplier_block_name(ctx, buyer_entities)
        withhold_name = name is None and _signatory_name(ctx, buyer_entities) is not None
    else:
        name = _signatory_name(ctx, buyer_entities) or _supplier_block_name(ctx, buyer_entities)
        withhold_name = False
    if name:
        header_raw["vendor_name"] = name
        checks["vendor_name"] = FieldCheck(Status.UNVERIFIED)

    po_labels = _PO_LABELS.get(doc_type, ())
    po = _first(ctx, po_labels, accept_po) if po_labels else None
    generic_po = False
    if po is None and doc_type is DocType.INVOICE:
        po = _first(
            ctx,
            _GENERIC_ORDER_LABELS,
            accept_po,
            exclude=_SELLER_ORDER_WORDS | {"CUST", "CUSTOMER", "BUYER", "BUYERS", "S"},
        )
        generic_po = po is not None
    if po is not None:
        header_raw["po_refs"] = po.value
        if generic_po:
            checks["po_refs"] = FieldCheck(
                Status.UNVERIFIED, "unqualified order label", cap=DERIVED_CONFIDENCE
            )
        else:
            checks["po_refs"] = FieldCheck(Status.CLEAR if _clear(po) else Status.UNVERIFIED)
        evidence["po_refs"] = _evidence(po)
    marketplace_orders = list(
        dict.fromkeys(
            match.group(0)
            for row in ctx.all_rows
            for match in _MARKETPLACE_ORDER.finditer(row.text)
        )
    )
    vendor_state = vendor[0][:2] if vendor is not None else None
    own_states = {gstin[:2] for gstin in own}
    buyer_state = (
        buyer[0][:2]
        if buyer is not None
        else (next(iter(own_states)) if len(own_states) == 1 else None)
    )
    interstate = (
        None if vendor_state is None or buyer_state is None else vendor_state != buyer_state
    )

    tables = extract_tables(document_pages)
    lines = [solve_line(_line_numbers(item)) for item in tables.items]
    summary = _summary_candidates(ctx, tables)

    def first_num(name: str) -> Num:
        values = summary[name]
        return Num(values[0][0] if values else None)

    total_quantity = None
    for row in tables.summary_rows:
        match = re.search(
            r"\bTOTAL\b\s+(\d{1,6}(?:\.\d{1,3})?)\s+(NO|NOS|PCS|EA|UNITS?)\b",
            norm_spaces(row.text).replace(" 0 ", " 0 "),
        )
        if match:
            total_quantity = Decimal(match.group(1))
    words = _words_totals(ctx)
    doc_numbers = DocumentNumbers(
        taxable=first_num("taxable"),
        cgst=first_num("cgst"),
        sgst=first_num("sgst"),
        igst=first_num("igst"),
        grand_total=first_num("grand"),
        round_off=first_num("round_off"),
        words_totals=words,
        total_quantity=total_quantity,
        taxable_candidates=[value for value, _c in summary["taxable"]],
        grand_candidates=[value for value, _c in summary["grand"]],
        tax_candidates={name: [v for v, _c in summary[name]] for name in ("cgst", "sgst", "igst")},
        interstate=interstate,
        gst_rates=tuple(gst_rates),
        rupee_glyph=_rupee_glyph(document_pages),
    )
    solve_document(doc_numbers, lines)
    lines = [solve_line(line) for line in lines]
    solve_document(doc_numbers, lines)

    for name, num in (
        ("taxable_total", doc_numbers.taxable),
        ("cgst", doc_numbers.cgst),
        ("sgst", doc_numbers.sgst),
        ("igst", doc_numbers.igst),
        ("grand_total", doc_numbers.grand_total),
        ("round_off", doc_numbers.round_off),
    ):
        if num.known:
            header_raw[name] = format(num.value, "f")
            checks[name] = FieldCheck(num.status, num.note)

    line_raw: list[dict[str, str]] = []
    line_extras: list[dict[str, Any]] = []
    line_checks: list[dict[str, FieldCheck]] = []
    for item, numbers in zip(tables.items, lines, strict=True):
        raw: dict[str, str] = {}
        line_check: dict[str, FieldCheck] = {}
        description = " ".join(item.description_rows).strip()
        if description:
            raw["description"] = description
        if item.item_code:
            raw["item_code"] = item.item_code
        if item.hsn is not None:
            raw["hsn_sac"] = item.hsn.value
        uom = " ".join(w.text for w in item.values.get(Col.UOM, [])).strip(" .|!")
        if uom and norm(uom) and not any(char.isdigit() for char in uom):
            raw["uom"] = uom
        for name, num in (
            ("quantity", numbers.quantity),
            ("rate", numbers.rate),
            ("amount", numbers.amount),
        ):
            if num.known:
                raw[name] = format(num.value, "f")
                line_check[name] = FieldCheck(num.status, num.note)
        if numbers.discount.known:
            raw["discount"] = format(abs(numbers.discount.value or 0), "f")
        extras: dict[str, Any] = {}
        if numbers.freight.known:
            extras["freight"] = format(numbers.freight.value, "f")
        line_raw.append(raw)
        line_extras.append(extras)
        line_checks.append(line_check)

    document, built_warnings = build_document(
        doc_type=doc_type,
        header_raw=header_raw,
        line_raw=line_raw,
        header_extras={
            "extraction": "layout",
            "source": source.value,
            **({"marketplace_order_no": marketplace_orders} if marketplace_orders else {}),
        },
        line_extras=line_extras,
        locator="layout",
        source=FieldSource.RULE,
        origin=FieldOrigin.OCR,
        base_confidence=OCR_CONFIDENCE if source == PageSource.OCR else TEXT_LAYER_CONFIDENCE,
        uom_aliases=uom_aliases,
        charge_keywords=charge_keywords,
    )
    warnings.extend(built_warnings)
    document = _apply_checks(document, checks, evidence, line_checks, source)
    if withhold_name:
        document, withheld = withhold_vendor_name(document, locator="layout")
        warnings.append(withheld)
    return Extraction(document, tuple(warnings), checks, line_checks, kinds)


def _apply_checks(
    document: Document,
    checks: dict[str, FieldCheck],
    evidence: dict[str, FieldEvidence | None],
    line_checks: list[dict[str, FieldCheck]],
    source: PageSource,
) -> Document:
    header_updates: dict[str, Any] = {}
    for name, check in checks.items():
        current = getattr(document.header, name, None)
        if current is None:
            continue
        confidence = _confidence(check.status, source)
        if check.cap is not None:
            confidence = min(confidence, check.cap)
        updates: dict[str, Any] = {"confidence": confidence}
        if evidence.get(name) is not None:
            updates["evidence"] = evidence[name]
        header_updates[name] = current.model_copy(update=updates)
    header = document.header.model_copy(update=header_updates)
    lines = []
    for line, check_map in zip(document.lines, line_checks, strict=False):
        updates = {}
        for name, check in check_map.items():
            current = getattr(line, name, None)
            if current is not None:
                updates[name] = current.model_copy(
                    update={"confidence": _confidence(check.status, source)}
                )
        lines.append(line.model_copy(update=updates))
    lines.extend(document.lines[len(line_checks) :])
    critical = ["doc_no", "doc_date", "grand_total"]
    missing = [name for name in critical if getattr(header, name, None) is None]
    if (
        getattr(header, "vendor_gstin", None) is None
        and getattr(header, "vendor_name", None) is None
    ):
        missing.append("vendor")
    if getattr(header, "taxable_total", None) is None:
        missing.append("taxable_total")
    low = [
        name
        for name in (*critical, "vendor_gstin", "taxable_total", "cgst", "sgst", "igst", "po_refs")
        if getattr(header, name, None) is not None
        and getattr(header, name).confidence < REVIEW_FLOOR
    ]
    line_low = [
        line.line_no
        for line in lines
        if any(
            getattr(line, name, None) is not None and getattr(line, name).confidence < REVIEW_FLOOR
            for name in ("quantity", "rate", "amount")
        )
        or line.amount is None
        # Goods are matched on quantity; a goods line without one cannot be checked.
        or (
            line.quantity is None
            and (line.line_type is None or line.line_type.value == LineType.GOODS)
        )
    ]
    needs = bool(missing or low or line_low or not lines)
    reason_codes = (ReasonCode.LOW_CONFIDENCE_DATA,) if needs else ()
    extras = dict(header.extras)
    # Every reason for review is listed, so a later confirmation (dual-read)
    # can only clear the flag when nothing else is outstanding.
    extras["review_fields"] = (
        sorted(set(missing + low)) + [f"line {n}" for n in line_low] + ([] if lines else ["lines"])
    )
    header = header.model_copy(update={"extras": extras})
    return document.model_copy(
        update={
            "header": header,
            "lines": tuple(lines),
            "needs_verification": needs,
            "reason_codes": reason_codes,
        }
    )


__all__ = [
    "Extraction",
    "FieldCheck",
    "PageKind",
    "classify_page",
    "extract_document",
    "page_document_number",
]
