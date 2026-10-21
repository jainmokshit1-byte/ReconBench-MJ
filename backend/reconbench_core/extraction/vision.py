"""Vision reader checks (accuracy plan AC-5): page-image readings → checked values.

A vision model reads masked page images and returns every value *as printed*,
with its page and a box (``[ymin, xmin, ymax, xmax]`` scaled 0–1000). Nothing
here trusts the model: each reading is normalised by the same deterministic
rules as the OCR reader, and the plan §7 hard rejects drop a reading outright:

* the page was not sent, the box is malformed, or the box lies in a masked area;
* the supplier GSTIN is a buyer entity (a role error) or fails its checksum;
* a tax type breaks the state rule (IGST intra-state, CGST/SGST inter-state);
* vision totals break arithmetic that the OCR totals satisfy, or a line's
  quantity × rate breaks arithmetic that the OCR line with the same amount keeps;
* address digits, marketplace or sales-order numbers offered as PO references;
* two different values for a single-valued field.

Standalone checks (LLM plan WI-BE4) need no OCR: totals arithmetic, the sum of
line amounts against the taxable total, quantity x rate per line and the D42
date window. A failure does not drop the value: the item keeps it and is marked
``check_failed`` with the reason, so it stays a visible suggestion but is never
merged. Values the model saw but could not read are listed as ``unreadable``
(name, page, box; no value) and counted.

The checked reading becomes an unverified :class:`Document` (source ``ai``,
confidence below the review gate), passed through the AC-1 party/GST guard.
In AC-5 these values are suggestions only; AC-6 decides when they may confirm.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from types import MappingProxyType
from typing import Any, Literal

from reconbench_core.extraction.date_window import date_in_window
from reconbench_core.extraction.extract import accept_date, accept_doc_no, accept_po
from reconbench_core.extraction.handwritten_refs import typed_refs
from reconbench_core.normalize.gstin import validate_gstin
from reconbench_core.normalize.numbers import parse_decimal
from reconbench_core.parsers.parties import (
    buyer_gstins,
    guard_document,
    is_buyer_name,
    name_problem,
)
from reconbench_core.parsers.types import BuyerEntityHint, ParserHints
from reconbench_core.schema import DocType, Document, FieldSource, ReasonCode

VISION_CONFIDENCE = Decimal("0.65")
"""Same level as an unchecked OCR reading: below the 0.70 review gate (D18)."""

BOX_SCALE = 1000
MASK_OVERLAP_PERCENT = 30
_TOLERANCE = Decimal("1.00")
_LINE_TOLERANCE = Decimal("0.02")

FieldKind = Literal["text", "doc_no", "ref", "po_ref", "irn", "date", "money", "gstin", "currency"]
CellKind = Literal["text", "ref", "number", "money", "rate"]

FIELD_KINDS: Mapping[str, FieldKind] = MappingProxyType(
    {
        # identity
        "doc_no": "doc_no",
        "doc_date": "date",
        "irn": "irn",
        "ack_no": "ref",
        "ack_date": "date",
        "e_way_bill_no": "ref",
        "challan_no": "ref",
        "supplier_invoice_no": "ref",
        "supplier_invoice_date": "date",
        "place_of_supply": "text",
        "currency": "currency",
        "reverse_charge": "text",
        "payment_terms": "text",
        "delivery_date": "date",
        # parties and roles
        "supplier_name": "text",
        "supplier_gstin": "gstin",
        "supplier_state": "text",
        "buyer_name": "text",
        "buyer_gstin": "gstin",
        "buyer_state": "text",
        "ship_to_name": "text",
        "ship_to_gstin": "gstin",
        "marketplace_name": "text",
        # typed references
        "po_ref": "po_ref",
        "grn_ref": "po_ref",
        "sales_order_ref": "ref",
        "marketplace_order_ref": "ref",
        "quotation_ref": "ref",
        "other_ref": "ref",
        # totals
        "taxable_total": "money",
        "cgst_total": "money",
        "sgst_total": "money",
        "igst_total": "money",
        "cess_total": "money",
        "tcs_total": "money",
        "other_charge": "money",
        "discount_total": "money",
        "round_off": "money",
        "grand_total": "money",
        "amount_in_words": "text",
    }
)
MULTI_FIELDS = frozenset(
    {
        "po_ref",
        "grn_ref",
        "sales_order_ref",
        "marketplace_order_ref",
        "quotation_ref",
        "other_ref",
        "other_charge",
    }
)
LINE_CELLS: Mapping[str, CellKind] = MappingProxyType(
    {
        "line_no": "text",
        "description": "text",
        "item_code": "ref",
        "hsn_sac": "ref",
        "quantity": "number",
        "uom": "text",
        "rate": "money",
        "discount": "money",
        "amount": "money",
        "tax_rate": "rate",
        "cgst": "money",
        "sgst": "money",
        "igst": "money",
        "received_qty": "number",
        "accepted_qty": "number",
        "rejected_qty": "number",
        "serials": "text",
        "po_ref": "ref",
    }
)
DOC_KINDS = ("invoice", "credit_note", "debit_note", "purchase_order", "goods_receipt", "other")
PAGE_KINDS = (
    "invoice",
    "credit_note",
    "debit_note",
    "purchase_order",
    "goods_receipt",
    "delivery_challan",
    "eway_bill",
    "annexure",
    "other",
)
ANNOTATION_KINDS = ("handwritten", "stamp", "other")
ANNOTATION_LOOKS = (
    "po_number",
    "grn_number",
    "invoice_number",
    "date",
    "amount",
    "initials_or_signature",
    "note",
    "unknown",
)
ANNOTATION_LOOKS_V5 = (*ANNOTATION_LOOKS, "service_po_number")
"""``vision.schema.v5`` (WI-Q2): a service purchase order number becomes its own type.
The v4 list above stays as it is, so v4 requests and their cache keys are unchanged."""
_PARTY_NAMES = ("supplier_name", "ship_to_name", "marketplace_name")
_TAX_TOTALS = ("cgst_total", "sgst_total", "igst_total", "cess_total", "tcs_total")
_TOTALS = (
    "taxable_total",
    *_TAX_TOTALS,
    "other_charge",
    "discount_total",
    "round_off",
    "grand_total",
)
# A printed value the model could not read names one of these (header field or line cell).
UNREADABLE_NAMES = frozenset(FIELD_KINDS) | frozenset(LINE_CELLS)
_WINDOW_DATES = ("doc_date", "supplier_invoice_date", "ack_date")
_MAX_TEXT = 500
_MAX_UOM = 20
# A line row is kept when it carries at least one of these (a GRN row has no amount).
_LINE_CONTENT = frozenset(
    {"description", "amount", "quantity", "received_qty", "accepted_qty", "rejected_qty"}
)
_NUMBER_TOKEN = re.compile(r"\(?-?[\d,]*\.?\d+\)?")
_CURRENCY_WORDS = re.compile(r"(?i)(?:₹|\bRs\.?|\bINR\b|/-|\bonly\b|\bDr\.?|\bCr\.?)")
_REF_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_.\-]*")
_IRN = re.compile(r"[0-9a-fA-F]{64}")
_CURRENCY_CODE = re.compile(r"[A-Z]{3}")


# --------------------------------------------------------------------------- reading


@dataclass(frozen=True, slots=True)
class Box:
    """A box on one page: ``[ymin, xmin, ymax, xmax]`` scaled 0–1000 (Gemini convention)."""

    ymin: int
    xmin: int
    ymax: int
    xmax: int

    @classmethod
    def parse(cls, values: Sequence[int]) -> Box | None:
        if len(values) != 4 or not all(isinstance(value, int) for value in values):
            return None
        ymin, xmin, ymax, xmax = values
        if not (0 <= ymin < ymax <= BOX_SCALE and 0 <= xmin < xmax <= BOX_SCALE):
            return None
        return cls(ymin, xmin, ymax, xmax)

    @property
    def area(self) -> int:
        return (self.ymax - self.ymin) * (self.xmax - self.xmin)

    def overlap(self, other: Box) -> int:
        height = min(self.ymax, other.ymax) - max(self.ymin, other.ymin)
        width = min(self.xmax, other.xmax) - max(self.xmin, other.xmin)
        return height * width if height > 0 and width > 0 else 0

    def as_list(self) -> list[int]:
        return [self.ymin, self.xmin, self.ymax, self.xmax]


@dataclass(frozen=True, slots=True)
class VisionField:
    name: str
    text: str
    page: int
    box: Sequence[int]
    label: str | None = None


@dataclass(frozen=True, slots=True)
class VisionLine:
    page: int
    box: Sequence[int]
    cells: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class VisionAnnotation:
    kind: str
    text: str
    looks_like: str
    page: int
    box: Sequence[int]


@dataclass(frozen=True, slots=True)
class VisionPageKind:
    page: int
    kind: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class VisionUnreadable:
    """A value printed on the page that the model could not read with certainty."""

    name: str
    page: int
    box: Sequence[int]


@dataclass(frozen=True, slots=True)
class VisionReading:
    """The provider answer after strict parsing (no values interpreted yet)."""

    doc_kind: str
    pages: tuple[VisionPageKind, ...] = ()
    fields: tuple[VisionField, ...] = ()
    lines: tuple[VisionLine, ...] = ()
    annotations: tuple[VisionAnnotation, ...] = ()
    unreadable: tuple[VisionUnreadable, ...] = ()

    @property
    def item_count(self) -> int:
        return len(self.fields) + len(self.lines) + len(self.annotations)


@dataclass(frozen=True, slots=True)
class SentPage:
    """A page that was sent, with the regions that were blacked out on it."""

    page: int
    masks: tuple[Box, ...] = ()


# --------------------------------------------------------------------------- results


Value = str | Decimal | date


@dataclass(frozen=True, slots=True)
class AcceptedField:
    name: str
    kind: FieldKind
    text: str
    value: Value
    page: int
    box: Box
    label: str | None = None
    check_failed: tuple[str, ...] = ()
    """Standalone checks this value failed: kept as a suggestion, never merged."""


@dataclass(frozen=True, slots=True)
class AcceptedCell:
    name: str
    text: str
    value: Value


@dataclass(frozen=True, slots=True)
class AcceptedLine:
    index: int
    """1-based position in reading order among the accepted lines."""
    page: int
    box: Box
    cells: tuple[AcceptedCell, ...]
    check_failed: tuple[str, ...] = ()

    def cell(self, name: str) -> AcceptedCell | None:
        return next((cell for cell in self.cells if cell.name == name), None)

    def value(self, name: str) -> Value | None:
        found = self.cell(name)
        return found.value if found is not None else None


@dataclass(frozen=True, slots=True)
class AcceptedAnnotation:
    kind: str
    text: str
    looks_like: str
    page: int
    box: Box


@dataclass(frozen=True, slots=True)
class AcceptedUnreadable:
    name: str
    page: int
    box: Box


@dataclass(frozen=True, slots=True)
class VisionCheck:
    doc_kind: str
    fields: tuple[AcceptedField, ...]
    lines: tuple[AcceptedLine, ...]
    annotations: tuple[AcceptedAnnotation, ...]
    page_kinds: tuple[VisionPageKind, ...]
    dropped: Mapping[str, int] = field(default_factory=dict)
    unreadable: tuple[AcceptedUnreadable, ...] = ()
    check_failed: Mapping[str, int] = field(default_factory=dict)
    """Items marked per standalone check (an item may count under several checks)."""

    @property
    def accepted_count(self) -> int:
        return len(self.fields) + len(self.lines) + len(self.annotations)

    def values(self, name: str) -> list[Value]:
        return [item.value for item in self.fields if item.name == name]

    def first(self, name: str) -> AcceptedField | None:
        return next((item for item in self.fields if item.name == name), None)


# --------------------------------------------------------------------------- normalising


def _clean_text(text: str) -> str:
    return " ".join(text.split())


def parse_money(text: str) -> Decimal | None:
    """An amount as printed ("₹ 1,23,456.00", "(-)0.31", "400/-") or ``None``."""

    cleaned = _CURRENCY_WORDS.sub(" ", text).replace("(-)", "-").replace(" ", "")
    cleaned = cleaned.strip(":;=")
    if not cleaned or "%" in cleaned:
        return None
    try:
        return parse_decimal(cleaned)
    except ValueError:
        return None


def parse_quantity(text: str) -> Decimal | None:
    """The leading number of a quantity cell ("2 Nos", "1.000 PCS")."""

    match = _NUMBER_TOKEN.search(text.replace(" ", "") if text.strip()[:1].isdigit() else text)
    if match is None:
        return None
    candidate = match.group(0)
    # A grouped quantity with spaces ("1 000") never occurs in these documents.
    try:
        return parse_decimal(candidate)
    except ValueError:
        return None


def parse_rate_percent(text: str) -> Decimal | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%?", text)
    if match is None:
        return None
    try:
        value = Decimal(match.group(1))
    except InvalidOperation:
        return None
    return value if Decimal("0") <= value <= Decimal("100") else None


def _plain_ref(text: str) -> str | None:
    value = text.strip(" :.-|#;,_")
    if " " in value and all(part.isdigit() for part in value.split()):
        value = value.replace(" ", "")  # "1234 5678 9012" e-way bill groups
    if not value or len(value) > 64 or not any(char.isdigit() for char in value):
        return None
    return value if _REF_SHAPE.fullmatch(value) else None


def normalize_field(kind: FieldKind, text: str) -> Value | None:
    """The typed value of a printed field, by the same rules as the OCR reader."""

    if kind == "text":
        value = _clean_text(text)
        return value or None
    if kind == "doc_no":
        return accept_doc_no(_clean_text(text))
    if kind == "po_ref":
        return accept_po(_clean_text(text))
    if kind == "ref":
        return _plain_ref(_clean_text(text))
    if kind == "irn":
        compact = re.sub(r"\s+", "", text)
        return compact.lower() if _IRN.fullmatch(compact) else None
    if kind == "date":
        return accept_date(text)
    if kind == "money":
        return parse_money(text)
    if kind == "currency":
        compact = text.strip().upper()
        if compact in {"₹", "RS", "RS.", "RUPEES"}:
            return "INR"
        return compact if _CURRENCY_CODE.fullmatch(compact) else None
    # gstin
    facts = validate_gstin(re.sub(r"[^0-9A-Za-z]", "", text))
    return facts.normalized if facts.is_valid else None


def _cell_value(kind: CellKind, text: str) -> Value | None:
    if kind == "text":
        value = _clean_text(text)
        return value or None
    if kind == "ref":
        value = _clean_text(text)
        return value or None
    if kind == "number":
        return parse_quantity(text)
    if kind == "rate":
        return parse_rate_percent(text)
    return parse_money(text)


# --------------------------------------------------------------------------- checks


def _masked(box: Box, page: SentPage) -> bool:
    if not page.masks or box.area == 0:
        return False
    covered = sum(box.overlap(mask) for mask in page.masks)
    return covered * 100 > box.area * MASK_OVERLAP_PERCENT


def _place(
    page_no: int,
    raw_box: Sequence[int],
    pages: Mapping[int, SentPage],
    dropped: Counter[str],
) -> Box | None:
    page = pages.get(page_no)
    if page is None:
        dropped["bad_page"] += 1
        return None
    box = Box.parse(raw_box)
    if box is None:
        dropped["bad_box"] += 1
        return None
    if _masked(box, page):
        dropped["in_mask"] += 1
        return None
    return box


def _state(gstin: object) -> str | None:
    return str(gstin)[:2] if isinstance(gstin, str) and len(gstin) == 15 else None


def _ocr_value(ocr: Document | None, name: str) -> Decimal | None:
    if ocr is None:
        return None
    item = getattr(ocr.header, name, None)
    if item is None or item.value is None:
        return None
    try:
        return Decimal(item.value)
    except (InvalidOperation, TypeError):
        return None


def _totals_consistent(values: Mapping[str, Decimal]) -> bool | None:
    taxable, grand = values.get("taxable_total"), values.get("grand_total")
    if taxable is None or grand is None:
        return None
    total = taxable
    for name in (*_TAX_TOTALS, "other_charge", "round_off"):
        total += values.get(name, Decimal("0"))
    total -= values.get("discount_total", Decimal("0"))
    return abs(total - grand) <= _TOLERANCE


def _ocr_totals(ocr: Document | None) -> dict[str, Decimal] | None:
    names = {
        "taxable_total": "taxable_total",
        "cgst_total": "cgst",
        "sgst_total": "sgst",
        "igst_total": "igst",
        "round_off": "round_off",
        "grand_total": "grand_total",
    }
    values = {
        vision: amount
        for vision, header in names.items()
        if (amount := _ocr_value(ocr, header)) is not None
    }
    return values if _totals_consistent(values) else None


def _line_consistent(quantity: object, rate: object, amount: object) -> bool | None:
    if not all(isinstance(value, Decimal) for value in (quantity, rate, amount)):
        return None
    assert isinstance(quantity, Decimal) and isinstance(rate, Decimal)
    assert isinstance(amount, Decimal)
    tolerance = max(_LINE_TOLERANCE, abs(amount) * Decimal("0.005"))
    return abs(quantity * rate - amount) <= tolerance


def _ocr_consistent_line(ocr: Document | None, amount: Decimal) -> bool:
    if ocr is None:
        return False
    for line in ocr.lines:
        if line.amount is None or Decimal(line.amount.value) != amount:
            continue
        quantity = line.quantity.value if line.quantity is not None else None
        rate = line.rate.value if line.rate is not None else None
        if _line_consistent(quantity, rate, Decimal(line.amount.value)):
            return True
    return False


def check_reading(
    reading: VisionReading,
    *,
    pages: Mapping[int, SentPage],
    buyer_entities: Sequence[BuyerEntityHint] = (),
    ocr: Document | None = None,
    as_of: date | None = None,
) -> VisionCheck:
    """Apply the structural checks, normalisation and plan §7 hard rejects, then mark
    the values that fail a standalone check (the date window only with ``as_of``)."""

    dropped: Counter[str] = Counter()
    buyers = buyer_gstins(buyer_entities)

    candidates: list[AcceptedField] = []
    for item in reading.fields:
        kind = FIELD_KINDS.get(item.name)
        if kind is None:
            dropped["unknown_field"] += 1
            continue
        text = item.text.strip()
        if not text or len(text) > _MAX_TEXT:
            dropped["empty"] += 1
            continue
        box = _place(item.page, item.box, pages, dropped)
        if box is None:
            continue
        value = normalize_field(kind, text)
        if value is None:
            dropped["gstin_invalid" if kind == "gstin" else "bad_value"] += 1
            continue
        if item.name == "supplier_gstin" and value in buyers:
            dropped["supplier_is_buyer"] += 1
            continue
        if item.name in _PARTY_NAMES and (
            name_problem(str(value)) is not None or is_buyer_name(str(value), buyer_entities)
        ):
            dropped["bad_name"] += 1
            continue
        candidates.append(
            AcceptedField(
                name=item.name,
                kind=kind,
                text=text,
                value=value,
                page=item.page,
                box=box,
                label=_clean_text(item.label) if item.label else None,
            )
        )

    fields: list[AcceptedField] = []
    by_name: dict[str, list[AcceptedField]] = {}
    for candidate in candidates:
        by_name.setdefault(candidate.name, []).append(candidate)
    resolved: set[tuple[str, Value]] = set()
    for name, group in by_name.items():
        if name not in MULTI_FIELDS and len({entry.value for entry in group}) > 1:
            chosen = _resolve_conflict(name, group, ocr)
            if chosen is None:
                dropped["conflict"] += len(group)
                continue
            # D77: keep one value, marked ``page_conflict`` (a suggestion, never merged).
            dropped["conflict"] += sum(1 for entry in group if entry.value != chosen)
            group = [entry for entry in group if entry.value == chosen]
            resolved.add((name, chosen))
        seen: set[Value] = set()
        for entry in group:
            if entry.value in seen and name not in {"other_charge"}:
                dropped["duplicate"] += 1
                continue
            seen.add(entry.value)
            fields.append(entry)

    # GST structure: the tax type must follow the supplier and buyer states.
    supplier_state = _state(next((f.value for f in fields if f.name == "supplier_gstin"), None))
    if supplier_state is None and ocr is not None and ocr.header.vendor_gstin is not None:
        supplier_state = _state(ocr.header.vendor_gstin.value)
    buyer_state = _state(next((f.value for f in fields if f.name == "buyer_gstin"), None))
    if buyer_state is None:
        states = {gstin[:2] for gstin in buyers}
        buyer_state = next(iter(states)) if len(states) == 1 else None
    wrong_taxes: set[str] = set()
    if supplier_state is not None and buyer_state is not None:
        wrong_taxes = (
            {"igst_total"} if supplier_state == buyer_state else {"cgst_total", "sgst_total"}
        )
    kept: list[AcceptedField] = []
    for entry in fields:
        if entry.name in wrong_taxes and isinstance(entry.value, Decimal) and entry.value != 0:
            dropped["gst_type"] += 1
            continue
        kept.append(entry)
    fields = kept

    # Totals that break arithmetic the OCR totals satisfy are not used.
    vision_totals: dict[str, Decimal] = {}
    for entry in fields:
        if entry.name in _TOTALS and isinstance(entry.value, Decimal):
            vision_totals[entry.name] = vision_totals.get(entry.name, Decimal("0")) + entry.value
    ocr_totals = _ocr_totals(ocr)
    if ocr_totals is not None and _totals_consistent(vision_totals) is False:
        conflicting = {
            name
            for name, amount in vision_totals.items()
            if name in ocr_totals and ocr_totals[name] != amount
        } or set(vision_totals)
        dropped["arithmetic_conflict"] += sum(1 for entry in fields if entry.name in conflicting)
        fields = [entry for entry in fields if entry.name not in conflicting]

    lines: list[AcceptedLine] = []
    line_taxes = {"igst"} if "igst_total" in wrong_taxes else set()
    if "cgst_total" in wrong_taxes:
        line_taxes = {"cgst", "sgst"}
    for line in reading.lines:
        box = _place(line.page, line.box, pages, dropped)
        if box is None:
            continue
        cells: list[AcceptedCell] = []
        for name, raw in line.cells.items():
            cell_kind = LINE_CELLS.get(name)
            text = raw.strip() if isinstance(raw, str) else ""
            if cell_kind is None or not text or len(text) > _MAX_TEXT:
                continue
            cell_value = _cell_value(cell_kind, text)
            if name == "uom" and len(text) > _MAX_UOM:
                cell_value = None  # another column pushed into the unit cell
            if cell_value is None:
                dropped["bad_cell"] += 1
                continue
            if name in line_taxes and isinstance(cell_value, Decimal) and cell_value != 0:
                dropped["gst_type"] += 1
                continue
            cells.append(AcceptedCell(name, text, cell_value))
        values = {cell.name: cell.value for cell in cells}
        if not values.keys() & _LINE_CONTENT:
            dropped["empty_line"] += 1
            continue
        amount = values.get("amount")
        if (
            isinstance(amount, Decimal)
            and _line_consistent(values.get("quantity"), values.get("rate"), amount) is False
            and _ocr_consistent_line(ocr, amount)
        ):
            dropped["line_arithmetic"] += 1
            cells = [cell for cell in cells if cell.name not in {"quantity", "rate"}]
        lines.append(AcceptedLine(len(lines) + 1, line.page, box, tuple(cells)))

    annotations: list[AcceptedAnnotation] = []
    for note in reading.annotations:
        text = _clean_text(note.text)
        if not text or len(text) > _MAX_TEXT:
            dropped["empty"] += 1
            continue
        if note.kind not in ANNOTATION_KINDS or note.looks_like not in ANNOTATION_LOOKS_V5:
            dropped["bad_annotation"] += 1
            continue
        box = _place(note.page, note.box, pages, dropped)
        if box is None:
            continue
        annotations.append(AcceptedAnnotation(note.kind, text, note.looks_like, note.page, box))

    unreadable: list[AcceptedUnreadable] = []
    for missing in reading.unreadable:
        if missing.name not in UNREADABLE_NAMES:
            dropped["unreadable_unknown"] += 1
            continue
        unplaced: Counter[str] = Counter()
        box = _place(missing.page, missing.box, pages, unplaced)
        if box is None:
            dropped.update({f"unreadable_{reason}": count for reason, count in unplaced.items()})
            continue
        unreadable.append(AcceptedUnreadable(missing.name, missing.page, box))

    fields, lines, failed = _standalone_checks(fields, lines, as_of)
    if resolved:
        fields = [
            replace(entry, check_failed=(*entry.check_failed, "page_conflict"))
            if (entry.name, entry.value) in resolved
            else entry
            for entry in fields
        ]
        failed["page_conflict"] += sum(
            1 for entry in fields if (entry.name, entry.value) in resolved
        )
    page_kinds = tuple(
        item for item in reading.pages if item.page in pages and item.kind in PAGE_KINDS
    )
    return VisionCheck(
        doc_kind=reading.doc_kind if reading.doc_kind in DOC_KINDS else "other",
        fields=tuple(fields),
        lines=tuple(lines),
        annotations=tuple(annotations),
        page_kinds=page_kinds,
        dropped=MappingProxyType(dict(dropped)),
        unreadable=tuple(unreadable),
        check_failed=MappingProxyType(dict(failed)),
    )


def _fold_value(value: object) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value).upper())


def _ocr_agrees(name: str, value: Value, ocr: Document | None) -> bool:
    """Whether the rules reader read the same value for this header field."""

    target = _HEADER_MAP.get(name)
    item = getattr(ocr.header, target, None) if ocr is not None and target else None
    if item is None or item.value is None:
        return False
    if isinstance(value, Decimal):
        try:
            return Decimal(str(item.value)) == value
        except (InvalidOperation, ValueError):
            return False
    if isinstance(value, date):
        return bool(item.value == value)
    return bool(_fold_value(value)) and _fold_value(value) == _fold_value(item.value)


def _resolve_conflict(
    name: str, group: Sequence[AcceptedField], ocr: Document | None
) -> Value | None:
    """The one value to keep when a single-valued field was read with different values.

    D77: one page per call returns the same field from several pages. Keep the value
    the rules reader also read; otherwise, when the values come from different pages,
    the first page's value (totals: the last page's), if that page gave one value.
    ``None`` (drop them all, as before) for anything else.
    """

    values = list(dict.fromkeys(entry.value for entry in group))
    agreeing = [value for value in values if _ocr_agrees(name, value, ocr)]
    if len(agreeing) == 1:
        return agreeing[0]
    pages = {entry.page for entry in group}
    if len(pages) < 2:
        return None
    page = max(pages) if name in _TOTALS else min(pages)
    on_page = {entry.value for entry in group if entry.page == page}
    return on_page.pop() if len(on_page) == 1 else None


def _standalone_checks(
    fields: list[AcceptedField], lines: list[AcceptedLine], as_of: date | None
) -> tuple[list[AcceptedField], list[AcceptedLine], Counter[str]]:
    """Mark (never drop) values that fail a check needing no OCR (WI-BE4)."""

    field_marks: list[list[str]] = [[] for _ in fields]
    line_marks: list[list[str]] = [[] for _ in lines]

    totals: dict[str, Decimal] = {}
    for entry in fields:
        if entry.name in _TOTALS and isinstance(entry.value, Decimal):
            totals[entry.name] = totals.get(entry.name, Decimal("0")) + entry.value
    if _totals_consistent(totals) is False:
        for index, entry in enumerate(fields):
            if entry.name in _TOTALS:
                field_marks[index].append("totals_arithmetic")

    taxable = totals.get("taxable_total")
    amounts = [amount for line in lines if isinstance(amount := line.value("amount"), Decimal)]
    if (
        taxable is not None
        and lines
        and len(amounts) == len(lines)
        and abs(sum(amounts, Decimal("0")) - taxable) > _TOLERANCE
    ):
        for index, entry in enumerate(fields):
            if entry.name == "taxable_total":
                field_marks[index].append("lines_vs_taxable")
        for marks in line_marks:
            marks.append("lines_vs_taxable")

    for index, line in enumerate(lines):
        quantity, rate, amount = line.value("quantity"), line.value("rate"), line.value("amount")
        if _line_consistent(quantity, rate, amount) is False:
            line_marks[index].append("line_arithmetic")

    if as_of is not None:
        for index, entry in enumerate(fields):
            if entry.name in _WINDOW_DATES and not date_in_window(entry.value, as_of):
                field_marks[index].append("date_window")

    failed: Counter[str] = Counter()
    for marks in (*field_marks, *line_marks):
        failed.update(marks)
    return (
        [
            replace(entry, check_failed=tuple(marks)) if marks else entry
            for entry, marks in zip(fields, field_marks, strict=True)
        ],
        [
            replace(line, check_failed=tuple(marks)) if marks else line
            for line, marks in zip(lines, line_marks, strict=True)
        ],
        failed,
    )


# --------------------------------------------------------------------------- document

_HEADER_MAP = {
    "doc_no": "doc_no",
    "doc_date": "doc_date",
    "supplier_name": "vendor_name",
    "supplier_gstin": "vendor_gstin",
    "buyer_gstin": "buyer_gstin",
    "currency": "currency",
    "taxable_total": "taxable_total",
    "cgst_total": "cgst",
    "sgst_total": "sgst",
    "igst_total": "igst",
    "round_off": "round_off",
    "grand_total": "grand_total",
}
_TYPE_MAP: Mapping[DocType, Mapping[str, str]] = {
    DocType.INVOICE: {
        "challan_no": "challan_no",
        "e_way_bill_no": "e_way_bill_no",
        "irn": "irn",
        "place_of_supply": "place_of_supply",
    },
    DocType.PO: {
        "ship_to_name": "ship_to",
        "payment_terms": "payment_terms",
        "delivery_date": "delivery_date",
    },
    DocType.GRN: {"challan_no": "challan_no", "supplier_invoice_no": "supplier_invoice_no"},
}
_REF_FIELDS: Mapping[DocType, Mapping[str, str]] = {
    DocType.INVOICE: {"po_ref": "po_refs", "grn_ref": "grn_refs"},
    DocType.PO: {},
    DocType.GRN: {"po_ref": "po_refs"},
}
_LINE_MAP = {
    "description": "description",
    "item_code": "item_code",
    "hsn_sac": "hsn_sac",
    "uom": "uom",
    "quantity": "quantity",
    "received_qty": "received_qty",
    "accepted_qty": "accepted_qty",
    "rejected_qty": "rejected_qty",
    "rate": "rate",
    "discount": "discount",
    "amount": "amount",
    "tax_rate": "tax_rate",
    "cgst": "cgst_amount",
    "sgst": "sgst_amount",
    "igst": "igst_amount",
    "po_ref": "po_ref",
}

# Read-only field maps shared with AC-6 consensus. Keep these views aligned
# with ``vision_document`` so the reader mapping has one source of truth.
VISION_HEADER_FIELDS: Mapping[str, str] = MappingProxyType(_HEADER_MAP)
VISION_TYPE_FIELDS: Mapping[DocType, Mapping[str, str]] = MappingProxyType(
    {key: MappingProxyType(dict(value)) for key, value in _TYPE_MAP.items()}
)
VISION_REF_FIELDS: Mapping[DocType, Mapping[str, str]] = MappingProxyType(
    {key: MappingProxyType(dict(value)) for key, value in _REF_FIELDS.items()}
)
VISION_LINE_FIELDS: Mapping[str, str] = MappingProxyType(_LINE_MAP)


def _evidence(page: int, box: Box, text: str) -> dict[str, Any]:
    return {
        "page": page,
        "snippet": text[:_MAX_TEXT],
        # x0, y0, x1, y1 on the 0–1000 page scale (extras.vision.box_space).
        "bbox": (
            Decimal(box.xmin),
            Decimal(box.ymin),
            Decimal(box.xmax),
            Decimal(box.ymax),
        ),
    }


def _field_value(value: object, page: int, box: Box, text: str) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": VISION_CONFIDENCE,
        "source": FieldSource.AI,
        "evidence": _evidence(page, box, text),
    }


def vision_document(
    check: VisionCheck,
    doc_type: DocType,
    *,
    buyer_entities: Sequence[BuyerEntityHint] = (),
    gst_rates: Sequence[Decimal] | None = None,
) -> Document:
    """The checked reading as an unverified document of ``doc_type`` (AC-1 guard applied)."""

    header: dict[str, Any] = {"doc_type": doc_type}
    names = {**_HEADER_MAP, **_TYPE_MAP[doc_type]}
    for item in check.fields:
        target = names.get(item.name)
        if target is None or target in header:
            continue
        value = item.value
        if item.kind == "gstin" or item.kind == "currency":
            value = str(value)
        header[target] = _field_value(value, item.page, item.box, item.text)
    for source, target in _REF_FIELDS[doc_type].items():
        refs = [item for item in check.fields if item.name == source]
        if refs:
            first = refs[0]
            header[target] = _field_value(
                tuple(dict.fromkeys(str(item.value) for item in refs)),
                first.page,
                first.box,
                "; ".join(item.text for item in refs),
            )
    used = set(names) | set(_REF_FIELDS[doc_type])
    others: dict[str, list[str]] = {}
    for item in check.fields:
        if item.name not in used:
            others.setdefault(item.name, []).append(
                item.value.isoformat() if isinstance(item.value, date) else str(item.value)
            )
    marketplace = others.get("marketplace_order_ref", [])
    handwritten_refs = [
        note.text
        for note in check.annotations
        if note.looks_like in {"po_number", "grn_number", "invoice_number", "service_po_number"}
    ]
    doc_date_field = header.get("doc_date")
    doc_date = doc_date_field.get("value") if isinstance(doc_date_field, dict) else None
    # Typed references (WI-Q2): annotations stay hints, never header fields (MG-9).
    handwritten_typed = typed_refs(
        (
            {"kind": note.kind, "text": note.text, "looks_like": note.looks_like, "page": note.page}
            for note in check.annotations
        ),
        doc_date=doc_date if isinstance(doc_date, date) else None,
    )
    header["extras"] = {
        "vision": {
            "doc_kind": check.doc_kind,
            "box_space": "page_1000_x0y0x1y1",
            "page_kinds": [
                {"page": item.page, "kind": item.kind, "title": item.title}
                for item in check.page_kinds
            ],
            "other_fields": others,
            "annotations": [
                {
                    "kind": note.kind,
                    "text": note.text,
                    "looks_like": note.looks_like,
                    "page": note.page,
                    "box": note.box.as_list(),
                }
                for note in check.annotations
            ],
            "dropped": dict(check.dropped),
        },
        **({"marketplace_order_no": marketplace[0]} if marketplace else {}),
        **({"annotation_refs": handwritten_refs} if handwritten_refs else {}),
        **(
            {"typed_refs": [item.as_dict() for item in handwritten_typed]}
            if handwritten_typed
            else {}
        ),
    }

    lines: list[dict[str, Any]] = []
    for line in check.lines:
        row: dict[str, Any] = {
            "line_no": line.index,
            "extras": {"vision": {"page": line.page, "box": line.box.as_list()}},
        }
        for cell in line.cells:
            if cell.name == "serials":
                row["extras"]["serials"] = cell.text
                continue
            target = _LINE_MAP.get(cell.name)
            if target is None:
                continue
            typed: object = cell.value
            if target in {"description", "item_code", "hsn_sac", "uom", "po_ref"}:
                typed = str(typed)
            row[target] = _field_value(typed, line.page, line.box, cell.text)
        if doc_type is DocType.GRN and "quantity" in row and "received_qty" not in row:
            row["received_qty"] = row.pop("quantity")
        lines.append(row)

    document = Document.model_validate(
        {
            "header": header,
            "lines": lines,
            "source_locator": "vision",
            "needs_verification": True,
            "reason_codes": (ReasonCode.LOW_CONFIDENCE_DATA,),
        }
    )
    hints = ParserHints(doc_type=doc_type, buyer_entities=tuple(buyer_entities))
    if gst_rates:
        hints = hints.model_copy(update={"gst_rates": tuple(gst_rates)})
    guarded, _warnings = guard_document(document, hints)
    return guarded


# --------------------------------------------------------------------------- alignment


def _description(value: object) -> str:
    return " ".join(str(value or "").upper().split())


def align_lines(
    current: Sequence[Mapping[str, Any]], lines: Iterable[AcceptedLine]
) -> list[int | None]:
    """For each vision line, the ``line_no`` of the current line it reads, or ``None``.

    ``current`` rows carry ``line_no``, ``amount``, ``quantity``, ``rate`` and
    ``description``. A pairing needs an equal amount, else equal quantity and
    rate, else a description similarity of at least 0.6; each current line is
    used once.
    """

    used: set[int] = set()
    result: list[int | None] = []

    def _decimal(value: object) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    rows = [
        (
            int(row["line_no"]),
            _decimal(row.get("amount")),
            _decimal(row.get("quantity")),
            _decimal(row.get("rate")),
            _description(row.get("description")),
        )
        for row in current
    ]
    for line in lines:
        amount, quantity, rate = line.value("amount"), line.value("quantity"), line.value("rate")
        description = _description(line.value("description"))
        match: int | None = None
        for line_no, row_amount, _qty, _rate, _desc in rows:
            if line_no in used:
                continue
            if amount is not None and row_amount is not None and amount == row_amount:
                match = line_no
                break
        if match is None:
            for line_no, _amount, row_qty, row_rate, _desc in rows:
                if line_no in used or quantity is None or rate is None:
                    continue
                if row_qty == quantity and row_rate == rate:
                    match = line_no
                    break
        if match is None and description:
            best = 0.0
            for line_no, _amount, _qty, _rate, row_desc in rows:
                if line_no in used or not row_desc:
                    continue
                ratio = SequenceMatcher(None, description, row_desc).ratio()
                if ratio >= 0.6 and ratio > best:
                    best, match = ratio, line_no
        if match is not None:
            used.add(match)
        result.append(match)
    return result


# --------------------------------------------------------------------------- page merge

_PAGE_LISTS = ("pages", "fields", "lines", "annotations", "unreadable")


@dataclass(frozen=True, slots=True)
class MergedAnswer:
    answer: dict[str, Any]
    dropped_off_page: int
    """Items an answer placed on a page other than the one image it was sent."""


def merge_page_readings(answers: Sequence[tuple[int, Mapping[str, Any]]]) -> MergedAnswer:
    """One document answer from answers that each read exactly one page (LLM plan WI-BE2).

    ``answers`` are ``(page sent, raw answer object)``. Each list item keeps its own
    page; an item that names any other page is dropped (the call saw no other page,
    the same rule as "page was not sent"). ``doc_kind`` is the first page's unless
    that page said ``other`` and a later page named a document kind. Nothing is
    deduplicated or reconciled across pages: :func:`check_reading` rejects two
    different values for a single-valued field.
    """

    ordered = sorted(answers, key=lambda item: item[0])
    merged: dict[str, Any] = {name: [] for name in _PAGE_LISTS}
    kinds: list[str] = []
    dropped = 0
    for page, answer in ordered:
        kind = answer.get("doc_kind")
        if isinstance(kind, str) and kind:
            kinds.append(kind)
        for name in _PAGE_LISTS:
            items = answer.get(name)
            if not isinstance(items, list):
                continue
            for item in items:
                # ``type is int``: a JSON ``true`` must not pass for page 1.
                if (
                    isinstance(item, Mapping)
                    and type(item.get("page")) is int
                    and (item["page"] == page)
                ):
                    merged[name].append(dict(item))
                else:
                    dropped += 1
    named = [kind for kind in kinds if kind != "other"]
    merged["doc_kind"] = named[0] if named else (kinds[0] if kinds else "other")
    return MergedAnswer(merged, dropped)


__all__ = [
    "ANNOTATION_KINDS",
    "ANNOTATION_LOOKS",
    "BOX_SCALE",
    "DOC_KINDS",
    "FIELD_KINDS",
    "LINE_CELLS",
    "MULTI_FIELDS",
    "PAGE_KINDS",
    "UNREADABLE_NAMES",
    "VISION_CONFIDENCE",
    "AcceptedAnnotation",
    "AcceptedCell",
    "AcceptedField",
    "AcceptedLine",
    "AcceptedUnreadable",
    "Box",
    "MergedAnswer",
    "SentPage",
    "VisionAnnotation",
    "VisionCheck",
    "VisionField",
    "VisionLine",
    "VisionPageKind",
    "VisionReading",
    "VisionUnreadable",
    "VISION_HEADER_FIELDS",
    "VISION_LINE_FIELDS",
    "VISION_REF_FIELDS",
    "VISION_TYPE_FIELDS",
    "align_lines",
    "check_reading",
    "merge_page_readings",
    "normalize_field",
    "parse_money",
    "parse_quantity",
    "vision_document",
]
