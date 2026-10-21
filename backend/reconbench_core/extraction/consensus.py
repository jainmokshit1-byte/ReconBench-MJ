"""Deterministic, per-field reader consensus for accuracy plan AC-6a.

This module is deliberately pure. It records trust decisions and, only when a
caller explicitly enables a field type, can raise a field's confidence after
independent readings agree. It never replaces a document value.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from reconbench_core.extraction.date_window import (
    DEFAULT_DATE_FUTURE_DAYS,
    DEFAULT_DATE_PAST_DAYS,
    date_in_window,
)
from reconbench_core.extraction.extract import accept_doc_no, accept_po
from reconbench_core.extraction.pages import split_unconfirmed
from reconbench_core.extraction.values import find_dates, gstins_in, has_lookalike
from reconbench_core.extraction.vision import (
    VISION_HEADER_FIELDS,
    VISION_LINE_FIELDS,
    VISION_REF_FIELDS,
    VISION_TYPE_FIELDS,
    Box,
    VisionCheck,
    align_lines,
    parse_money,
    parse_quantity,
)
from reconbench_core.normalize.gstin import validate_gstin
from reconbench_core.parsers.common import arithmetic_failures
from reconbench_core.parsers.parties import buyer_gstins, buyer_state, gst_problems
from reconbench_core.parsers.types import ParserHints
from reconbench_core.schema import (
    DocLine,
    DocType,
    Document,
    FieldSource,
    FieldValue,
    ReasonCode,
)

CONSENSUS_VERSION = "consensus.v1"
REOPEN_CONFIDENCE = Decimal("0.65")


class TrustLevel(StrEnum):
    L0 = "l0"
    L1 = "l1"
    L2 = "l2"
    L3 = "l3"
    REJECTED = "rejected"


class ReaderKind(StrEnum):
    STRUCTURED = "structured"
    MANUAL = "manual"
    QR_SIGNED = "qr_signed"
    QR = "qr"
    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    VISION = "vision"


class FieldType(StrEnum):
    DOC_NO = "doc_no"
    DOC_DATE = "doc_date"
    GSTIN = "gstin"
    AMOUNT = "amount"
    REFERENCE = "reference"
    LINE_AMOUNT = "line_amount"
    LINE_QUANTITY = "line_quantity"
    LINE_RATE = "line_rate"


class CropOutcome(StrEnum):
    AGREE = "agree"
    DISAGREE = "disagree"
    EMPTY = "empty"
    MASKED = "masked"
    UNAVAILABLE = "unavailable"


class DecisionRule(StrEnum):
    EXISTING = "existing"
    D42 = "d42"
    SINGLE = "single"
    CONFLICT = "conflict"
    REOPEN = "reopen"
    EXCLUDED = "excluded"
    VISION_ONLY = "vision_only"


HEADER_FIELD_TYPES: Mapping[str, FieldType] = MappingProxyType(
    {
        "doc_no": FieldType.DOC_NO,
        "doc_date": FieldType.DOC_DATE,
        "vendor_gstin": FieldType.GSTIN,
        "buyer_gstin": FieldType.GSTIN,
        "taxable_total": FieldType.AMOUNT,
        "cgst": FieldType.AMOUNT,
        "sgst": FieldType.AMOUNT,
        "igst": FieldType.AMOUNT,
        "round_off": FieldType.AMOUNT,
        "grand_total": FieldType.AMOUNT,
        "po_refs": FieldType.REFERENCE,
        "grn_refs": FieldType.REFERENCE,
        "challan_no": FieldType.REFERENCE,
        "supplier_invoice_no": FieldType.REFERENCE,
        "e_way_bill_no": FieldType.REFERENCE,
        "irn": FieldType.REFERENCE,
    }
)
LINE_FIELD_TYPES: Mapping[str, FieldType] = MappingProxyType(
    {
        "amount": FieldType.LINE_AMOUNT,
        "rate": FieldType.LINE_RATE,
        "quantity": FieldType.LINE_QUANTITY,
        "received_qty": FieldType.LINE_QUANTITY,
        "accepted_qty": FieldType.LINE_QUANTITY,
        "rejected_qty": FieldType.LINE_QUANTITY,
    }
)
NEVER_AUTOMATIC_HEADER = frozenset(
    {
        "vendor_name",
        "currency",
        "place_of_supply",
        "ship_to",
        "payment_terms",
        "delivery_date",
        "revision",
        "status",
    }
)
NEVER_AUTOMATIC_LINE = frozenset(
    {
        "description",
        "item_code",
        "hsn_sac",
        "uom",
        "discount",
        "tax_rate",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "po_ref",
        "po_line_ref",
        "line_type",
    }
)
VALUE_KINDS: Mapping[str, str] = MappingProxyType(
    {
        "doc_no": "doc_no",
        "doc_date": "date",
        "vendor_gstin": "gstin",
        "buyer_gstin": "gstin",
        "taxable_total": "money",
        "cgst": "money",
        "sgst": "money",
        "igst": "money",
        "round_off": "money",
        "grand_total": "money",
        "po_refs": "refs",
        "grn_refs": "refs",
        "challan_no": "ref",
        "supplier_invoice_no": "ref",
        "e_way_bill_no": "ref",
        "irn": "ref",
    }
)
LINE_VALUE_KINDS: Mapping[str, str] = MappingProxyType(
    {
        "amount": "money",
        **{name: "number" for name in LINE_FIELD_TYPES if name != "amount"},
    }
)
HARD_VALIDATORS = frozenset({"gstin_checksum", "buyer_entity", "gst_type"})

type ConsensusValue = str | Decimal | date | tuple[str, ...]


@dataclass(frozen=True, slots=True, order=True)
class FieldKey:
    scope: Literal["header", "line"]
    name: str
    line_no: int = 0

    def text(self) -> str:
        return (
            f"header:{self.name}" if self.scope == "header" else f"line:{self.line_no}:{self.name}"
        )

    @classmethod
    def parse(cls, text: str) -> FieldKey:
        parts = text.split(":")
        if len(parts) == 2 and parts[0] == "header" and parts[1]:
            return cls("header", parts[1])
        if len(parts) == 3 and parts[0] == "line" and parts[2]:
            try:
                line_no = int(parts[1])
            except ValueError as error:
                raise ValueError(f"invalid field key: {text}") from error
            if line_no < 1:
                raise ValueError(f"invalid field key: {text}")
            return cls("line", parts[2], line_no)
        raise ValueError(f"invalid field key: {text}")


@dataclass(frozen=True, slots=True)
class Reading:
    reader: ReaderKind
    value: ConsensusValue
    page: int | None = None
    box: tuple[int, int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class CropCheck:
    outcome: CropOutcome
    text: str = ""

    def __post_init__(self) -> None:
        if len(self.text) > 200:
            object.__setattr__(self, "text", self.text[:200])


@dataclass(frozen=True, slots=True)
class CropNeed:
    key: FieldKey
    kind: str
    value: ConsensusValue
    page: int
    box: tuple[int, int, int, int]
    mode: Literal["line", "block"]


@dataclass(frozen=True, slots=True)
class ConsensusPolicy:
    auto_verify: bool = False
    reopen: bool = False
    field_types: frozenset[FieldType] = frozenset()
    reopen_field_types: frozenset[FieldType] | None = None
    require_crop: bool = True
    as_of: date | None = None
    date_past_days: int = DEFAULT_DATE_PAST_DAYS
    date_future_days: int = DEFAULT_DATE_FUTURE_DAYS


@dataclass(frozen=True, slots=True)
class FieldDecision:
    key: FieldKey
    field_type: FieldType | None
    level: TrustLevel
    rule: DecisionRule
    reason: str
    readings: tuple[Reading, ...]
    supporting: tuple[ReaderKind, ...]
    crop: CropCheck | None = None
    failed_validators: tuple[str, ...] = ()
    auto_verified: bool = False
    reopened: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "key": self.key.text(),
            "field_type": self.field_type.value if self.field_type else None,
            "level": self.level.value,
            "rule": self.rule.value,
            "reason": self.reason,
            "readings": [
                {
                    "reader": reading.reader.value,
                    "value": _json_value(reading.value),
                    "page": reading.page,
                    "box": list(reading.box) if reading.box is not None else None,
                }
                for reading in self.readings
            ],
            "supporting": [reader.value for reader in self.supporting],
            "crop": (
                {"outcome": self.crop.outcome.value, "text": self.crop.text}
                if self.crop is not None
                else None
            ),
            "failed_validators": list(self.failed_validators),
            "auto_verified": self.auto_verified,
            "reopened": self.reopened,
        }


@dataclass(frozen=True, slots=True)
class ConsensusResult:
    version: str
    decisions: tuple[FieldDecision, ...]
    document: Document
    changed: bool
    vision_only_lines: int
    hints: Mapping[str, tuple[str, ...]]

    def summary(self) -> dict[str, object]:
        levels: Counter[str] = Counter(decision.level.value for decision in self.decisions)
        by_field: dict[str, Counter[str]] = {item.value: Counter() for item in FieldType}
        for decision in self.decisions:
            if decision.field_type is not None:
                by_field[decision.field_type.value][decision.level.value] += 1
        return {
            "levels": {name.value: levels[name.value] for name in TrustLevel},
            "by_field_type": {
                name: {level.value: counts[level.value] for level in TrustLevel}
                for name, counts in sorted(by_field.items())
            },
            "auto_verified": sum(decision.auto_verified for decision in self.decisions),
            "reopened": sum(decision.reopened for decision in self.decisions),
            "vision_only_lines": self.vision_only_lines,
        }

    def decisions_json(self) -> list[dict[str, object]]:
        return [decision.to_json() for decision in self.decisions]


def _json_value(value: ConsensusValue) -> str | list[str]:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    return value


def _compact(value: object) -> str:
    return "".join(str(value).split()).upper()


def _decimal_value(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _box_tuple(box: Box) -> tuple[int, int, int, int]:
    return (box.ymin, box.xmin, box.ymax, box.xmax)


def _decimal_agrees(left: ConsensusValue, right: ConsensusValue, quantum: Decimal) -> bool:
    left_amount = _decimal_value(left)
    right_amount = _decimal_value(right)
    return (
        left_amount is not None
        and right_amount is not None
        and left_amount.quantize(quantum) == right_amount.quantize(quantum)
    )


def values_agree(kind: str, left: ConsensusValue | None, right: ConsensusValue | None) -> bool:
    if left is None or right is None:
        return False
    try:
        if kind == "money":
            return _decimal_agrees(left, right, Decimal("0.01"))
        if kind == "number":
            return _decimal_agrees(left, right, Decimal("0.0001"))
    except (InvalidOperation, TypeError, ValueError):
        return False
    if kind == "date":
        return left == right
    if kind == "refs":
        return (
            isinstance(left, tuple)
            and isinstance(right, tuple)
            and bool(left)
            and frozenset(_compact(item) for item in left)
            == frozenset(_compact(item) for item in right)
        )
    if kind in {"doc_no", "ref", "gstin"}:
        return _compact(left) == _compact(right)
    return left == right


def _field_value(document: Document, key: FieldKey) -> FieldValue[object] | None:
    if key.scope == "header":
        return getattr(document.header, key.name, None)
    line = next((item for item in document.lines if item.line_no == key.line_no), None)
    return getattr(line, key.name, None) if line is not None else None


def _reading_overlaps_handwritten_mark(vision: VisionCheck | None, reading: Reading | None) -> bool:
    if vision is None or reading is None or reading.page is None or reading.box is None:
        return False
    top, left, bottom, right = reading.box
    for note in vision.annotations:
        if note.page != reading.page or note.kind not in {"handwritten", "stamp"}:
            continue
        mark_top, mark_left, mark_bottom, mark_right = _box_tuple(note.box)
        if max(top, mark_top) < min(bottom, mark_bottom) and max(left, mark_left) < min(
            right, mark_right
        ):
            return True
    return False


def _as_consensus(value: object) -> ConsensusValue:
    if isinstance(value, (str, Decimal, date)):
        return value
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return value
    raise TypeError(f"unsupported consensus value: {type(value).__name__}")


def _qr_value(name: str, value: str) -> ConsensusValue | None:
    kind = VALUE_KINDS.get(name)
    try:
        if kind == "date":
            return date.fromisoformat(value)
        if kind == "money":
            return Decimal(value)
        if kind == "refs":
            return tuple(value) if not value.startswith("(") else None
        return value
    except (ValueError, InvalidOperation):
        return None


def rules_readings(
    document: Document, *, structured: bool, rules_source: ReaderKind
) -> dict[FieldKey, tuple[Reading, ...]]:
    result: dict[FieldKey, tuple[Reading, ...]] = {}
    extras = document.header.extras
    einvoice = extras.get("einvoice", {})
    statuses = einvoice.get("fields", {}) if isinstance(einvoice, dict) else {}
    other = einvoice.get("other_readings", {}) if isinstance(einvoice, dict) else {}

    def add(key: FieldKey, field_value: FieldValue[object] | None) -> None:
        if field_value is None:
            return
        value = _as_consensus(field_value.value)
        if field_value.source is FieldSource.MANUAL:
            result[key] = (Reading(ReaderKind.MANUAL, value),)
            return
        # An AI-sourced value is a vision reading, never an OCR/rules or structured one, so it
        # cannot confirm itself and two LLM readings collapse to one reader kind (WI-G1).
        own = ReaderKind.VISION if field_value.source is FieldSource.AI else rules_source
        if structured and own is not ReaderKind.VISION:
            result[key] = (Reading(ReaderKind.STRUCTURED, value),)
            return
        status = statuses.get(key.name)
        if status == "signed":
            result[key] = (Reading(ReaderKind.QR_SIGNED, value),)
        elif status == "confirmed":
            result[key] = (Reading(own, value), Reading(ReaderKind.QR, value))
        elif status in {"corrected", "prefilled"}:
            result[key] = (Reading(ReaderKind.QR, value),)
        elif status == "conflict":
            readings = [Reading(ReaderKind.QR, value)]
            raw = other.get(key.name)
            alternate = _qr_value(key.name, str(raw)) if raw is not None else None
            if alternate is not None:
                readings.append(Reading(rules_source, alternate))
            result[key] = tuple(readings)
        else:
            result[key] = (Reading(own, value),)

    for name in (*HEADER_FIELD_TYPES, *NEVER_AUTOMATIC_HEADER):
        add(FieldKey("header", name), getattr(document.header, name, None))
    for line in document.lines:
        for name in (*LINE_FIELD_TYPES, *NEVER_AUTOMATIC_LINE):
            add(FieldKey("line", name, line.line_no), getattr(line, name, None))
    return result


def vision_readings(document: Document, check: VisionCheck) -> tuple[dict[FieldKey, Reading], int]:
    result: dict[FieldKey, Reading] = {}
    names = {**VISION_HEADER_FIELDS, **VISION_TYPE_FIELDS[document.header.doc_type]}
    for item in check.fields:
        target = names.get(item.name)
        if target is None or "page_conflict" in item.check_failed:
            # D77: a value chosen among conflicting readings never helps verify a field.
            continue
        key = FieldKey("header", target)
        result.setdefault(
            key,
            Reading(ReaderKind.VISION, _as_consensus(item.value), item.page, _box_tuple(item.box)),
        )
    for source, target in VISION_REF_FIELDS[document.header.doc_type].items():
        items = [item for item in check.fields if item.name == source]
        if not items:
            continue
        key = FieldKey("header", target)
        refs = tuple(dict.fromkeys(str(item.value) for item in items))
        first = items[0]
        result[key] = Reading(ReaderKind.VISION, refs, first.page, _box_tuple(first.box))

    current: list[dict[str, object]] = []
    line_by_no = {line.line_no: line for line in document.lines}
    for line in document.lines:
        qty = _plain(line.quantity) or _plain(line.received_qty) or _plain(line.accepted_qty)
        current.append(
            {
                "line_no": line.line_no,
                "amount": _plain(line.amount),
                "quantity": qty,
                "rate": _plain(line.rate),
                "description": _plain(line.description) or "",
            }
        )
    paired = align_lines(current, check.lines)
    unpaired = 0
    for vision_line, line_no in zip(check.lines, paired, strict=True):
        if line_no is None:
            unpaired += 1
            continue
        line = line_by_no[line_no]
        for cell in vision_line.cells:
            target = VISION_LINE_FIELDS.get(cell.name)
            if target is None:
                continue
            if (
                document.header.doc_type is DocType.GRN
                and target == "quantity"
                and line.quantity is None
            ):
                target = "received_qty"
            key = FieldKey("line", target, line_no)
            result.setdefault(
                key,
                Reading(
                    ReaderKind.VISION,
                    _as_consensus(cell.value),
                    vision_line.page,
                    _box_tuple(vision_line.box),
                ),
            )
    return result, unpaired


def _plain(field_value: object) -> object | None:
    return getattr(field_value, "value", None) if field_value is not None else None


def _arithmetic(document: Document) -> tuple[bool, tuple[str, ...]]:
    def plain(name: str) -> Decimal | None:
        value = _plain(getattr(document.header, name, None))
        return _decimal_value(value)

    return arithmetic_failures(
        [
            (
                line.line_no,
                _decimal_value(_plain(line.quantity)),
                _decimal_value(_plain(line.rate)),
                _decimal_value(_plain(line.discount)),
                _decimal_value(_plain(line.amount)),
            )
            for line in document.lines
        ],
        taxable_total=plain("taxable_total"),
        taxes=[plain("cgst"), plain("sgst"), plain("igst")],
        round_off=plain("round_off"),
        grand_total=plain("grand_total"),
    )


def field_validators(
    key: FieldKey,
    value: ConsensusValue,
    document: Document,
    *,
    hints: ParserHints,
    policy: ConsensusPolicy,
) -> tuple[str, ...]:
    failed: list[str] = []
    if split_unconfirmed(document):
        failed.append("split_unconfirmed")
    if key.scope == "header":
        name = key.name
        if name == "doc_no":
            if not isinstance(value, str) or accept_doc_no(value) != value:
                failed.append("doc_no_shape")
            if isinstance(value, str) and has_lookalike(value):
                failed.append("lookalike")
        elif name == "doc_date":
            if not date_in_window(
                value,
                policy.as_of,
                past_days=policy.date_past_days,
                future_days=policy.date_future_days,
            ):
                failed.append("date_window")
        elif name in {"vendor_gstin", "buyer_gstin"}:
            if not isinstance(value, str) or not validate_gstin(value).is_valid:
                failed.append("gstin_checksum")
            elif hints.buyer_entities:
                buyers = buyer_gstins(hints.buyer_entities)
                if (name == "vendor_gstin" and value.upper() in buyers) or (
                    name == "buyer_gstin" and value.upper() not in buyers
                ):
                    failed.append("buyer_entity")
        elif name in HEADER_FIELD_TYPES and HEADER_FIELD_TYPES[name] is FieldType.AMOUNT:
            problems = gst_problems(
                document,
                state=buyer_state(document.header, hints.buyer_entities),
                rates=hints.gst_rates,
            )
            mismatch = any(code == "GST_TYPE_MISMATCH" for _field, code, _message in problems)
            hard_field = {"cgst": "cgst", "sgst": "sgst", "igst": "igst"}.get(name)
            if (
                mismatch
                and hard_field
                and any(
                    field_name == hard_field and code == "GST_TYPE_MISMATCH"
                    for field_name, code, _message in problems
                )
            ):
                failed.append("gst_type")
            if any(code != "GST_TYPE_MISMATCH" for _field, code, _message in problems) or (
                mismatch and not hard_field
            ):
                failed.append("gst_structure")
            _ran, arithmetic = _arithmetic(document)
            if any(item in arithmetic for item in ("taxable total", "grand total")):
                failed.append("totals_arithmetic")
            if name != "round_off" and isinstance(value, Decimal) and value < 0:
                failed.append("non_negative")
        elif name in {"po_refs", "grn_refs"}:
            refs = value if isinstance(value, tuple) else ()
            if not refs or any(accept_po(item) != item or has_lookalike(item) for item in refs):
                failed.append("reference_shape")
        elif name in {"challan_no", "supplier_invoice_no"}:
            if not isinstance(value, str) or accept_doc_no(value) != value or has_lookalike(value):
                failed.append("reference_shape")
        elif name == "e_way_bill_no":
            if not isinstance(value, str) or re.fullmatch(r"\d{12}", value) is None:
                failed.append("reference_shape")
        elif name == "irn":
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
                failed.append("reference_shape")
    else:
        name = key.name
        line = next((item for item in document.lines if item.line_no == key.line_no), None)
        _ran, arithmetic = _arithmetic(document)
        if f"line {key.line_no} amount" in arithmetic:
            failed.append("line_arithmetic")
        if isinstance(value, Decimal) and (
            name in {"quantity", "received_qty", "accepted_qty", "rejected_qty"}
            and value <= 0
            or (name in {"rate", "amount"} and value < 0)
        ):
            failed.append("positive")
        if line is None:
            failed.append("line_missing")
    return tuple(failed)


def crop_outcome(
    kind: str, value: ConsensusValue, text: str, *, mode: Literal["line", "block"]
) -> CropOutcome:
    text = " ".join(text.split())
    if not text:
        return CropOutcome.EMPTY
    tokens = text.split()
    if mode == "block" and tokens and tokens[0].isdigit() and len(tokens[0]) <= 3:
        tokens = tokens[1:]
    candidates: list[ConsensusValue] = []
    if kind in {"money", "number"}:
        parser = parse_money if kind == "money" else parse_quantity
        candidates.extend(parsed for token in tokens if (parsed := parser(token)) is not None)
        candidates.extend(
            parsed
            for left, right in zip(tokens, tokens[1:], strict=False)
            if (parsed := parser(left + right)) is not None
        )
    elif kind == "date":
        candidates.extend(item.value for item in find_dates(text))
    elif kind == "gstin":
        candidates.extend(item.value for item in gstins_in(text) if not item.repaired)
    elif kind in {"doc_no", "ref", "refs"}:
        candidates.extend(tokens)
        candidates.extend(left + right for left, right in zip(tokens, tokens[1:], strict=False))
        candidates.append("".join(text.split()))
        if kind == "refs" and isinstance(value, tuple):
            return (
                CropOutcome.AGREE
                if all(
                    any(values_agree("ref", item, candidate) for candidate in candidates)
                    for item in value
                )
                else CropOutcome.DISAGREE
            )
    return (
        CropOutcome.AGREE
        if any(
            values_agree("ref" if kind == "refs" else kind, value, candidate)
            for candidate in candidates
        )
        else CropOutcome.DISAGREE
    )


def _line_needs_review(line: DocLine) -> bool:
    review_floor = Decimal("0.70")
    return (
        any(
            getattr(line, name) is not None
            and (
                getattr(line, name).confidence is None
                or getattr(line, name).confidence < review_floor
            )
            for name in ("quantity", "rate", "amount")
        )
        or line.amount is None
        or (line.quantity is None and (line.line_type is None or line.line_type.value == "goods"))
    )


def decide(
    document: Document,
    *,
    vision: VisionCheck | None,
    crops: Mapping[FieldKey, CropCheck] | None,
    hints: ParserHints,
    policy: ConsensusPolicy,
    structured: bool = False,
    rules_source: ReaderKind = ReaderKind.OCR,
) -> ConsensusResult:
    rules = rules_readings(document, structured=structured, rules_source=rules_source)
    vision_map, unpaired = vision_readings(document, vision) if vision else ({}, 0)
    decisions: list[FieldDecision] = []
    for key in sorted(set(rules) | set(vision_map)):
        field_value = _field_value(document, key)
        vis = vision_map.get(key)
        readings = rules.get(key, ()) + ((vis,) if vis is not None else ())
        field_type = (
            HEADER_FIELD_TYPES.get(key.name)
            if key.scope == "header"
            else LINE_FIELD_TYPES.get(key.name)
        )
        kind = (
            VALUE_KINDS.get(key.name) if key.scope == "header" else LINE_VALUE_KINDS.get(key.name)
        )
        if field_value is None:
            decisions.append(
                FieldDecision(
                    key,
                    field_type,
                    TrustLevel.L2,
                    DecisionRule.VISION_ONLY,
                    "vision_only",
                    readings,
                    (),
                )
            )
            continue
        current = _as_consensus(field_value.value)
        readers = tuple(reading.reader for reading in readings)
        if any(reading.reader is ReaderKind.MANUAL for reading in readings):
            decisions.append(
                FieldDecision(
                    key,
                    field_type,
                    TrustLevel.L0,
                    DecisionRule.EXISTING,
                    "manual",
                    readings,
                    readers,
                )
            )
            continue
        if any(reading.reader is ReaderKind.STRUCTURED for reading in readings):
            decisions.append(
                FieldDecision(
                    key,
                    field_type,
                    TrustLevel.L0,
                    DecisionRule.EXISTING,
                    "structured",
                    readings,
                    readers,
                )
            )
            continue
        if any(reading.reader is ReaderKind.QR_SIGNED for reading in readings):
            decisions.append(
                FieldDecision(
                    key,
                    field_type,
                    TrustLevel.L0,
                    DecisionRule.EXISTING,
                    "signed_qr",
                    readings,
                    readers,
                )
            )
            continue
        if _reading_overlaps_handwritten_mark(vision, vis):
            already_verified = (
                field_value.confidence is not None and field_value.confidence >= Decimal("0.98")
            )
            decisions.append(
                FieldDecision(
                    key,
                    field_type,
                    TrustLevel.L1 if already_verified else TrustLevel.L2,
                    DecisionRule.EXISTING if already_verified else DecisionRule.EXCLUDED,
                    "handwritten_or_stamped_mark",
                    readings,
                    tuple(reader for reader in readers if reader is not ReaderKind.VISION),
                )
            )
            continue
        if field_value.confidence is not None and field_value.confidence >= Decimal("0.98"):
            if (
                vis is not None
                and field_type is not None
                and kind is not None
                and not values_agree(kind, current, vis.value)
            ):
                decisions.append(
                    FieldDecision(
                        key,
                        field_type,
                        TrustLevel.L3,
                        DecisionRule.REOPEN,
                        "vision_disagrees_with_verified",
                        readings,
                        (),
                        crop=(crops or {}).get(key),
                        reopened=policy.reopen
                        and (
                            policy.reopen_field_types is None
                            or field_type in policy.reopen_field_types
                        ),
                    )
                )
            else:
                decisions.append(
                    FieldDecision(
                        key,
                        field_type,
                        TrustLevel.L1,
                        DecisionRule.EXISTING,
                        "rules_verified",
                        readings,
                        readers,
                    )
                )
            continue
        if field_type is None:
            decisions.append(
                FieldDecision(
                    key,
                    None,
                    TrustLevel.L2,
                    DecisionRule.EXCLUDED,
                    "never_automatic",
                    readings,
                    readers,
                )
            )
            continue
        supporters = tuple(
            reading.reader
            for reading in readings
            if kind is not None and values_agree(kind, current, reading.value)
        )
        dissent = tuple(
            reading
            for reading in readings
            if kind is None or not values_agree(kind, current, reading.value)
        )
        if dissent:
            decisions.append(
                FieldDecision(
                    key,
                    field_type,
                    TrustLevel.L3,
                    DecisionRule.CONFLICT,
                    "readers_disagree",
                    readings,
                    supporters,
                    crop=(crops or {}).get(key),
                )
            )
            continue
        if len(set(supporters)) < 2:
            decisions.append(
                FieldDecision(
                    key,
                    field_type,
                    TrustLevel.L2,
                    DecisionRule.SINGLE,
                    "one_reader",
                    readings,
                    supporters,
                )
            )
            continue
        failed = field_validators(key, current, document, hints=hints, policy=policy)
        crop = (crops or {}).get(key)
        if set(failed) & HARD_VALIDATORS:
            level, reason = TrustLevel.REJECTED, "hard_validator"
        elif failed:
            level, reason = TrustLevel.L2, "validator_failed"
        elif policy.require_crop and (crop is None or crop.outcome is not CropOutcome.AGREE):
            level = TrustLevel.L2
            reason = "crop_" + (crop.outcome.value if crop is not None else "missing")
        else:
            level, reason = TrustLevel.L1, "two_readers_agree"
        decisions.append(
            FieldDecision(
                key,
                field_type,
                level,
                DecisionRule.D42,
                reason,
                readings,
                supporters,
                crop,
                failed,
                auto_verified=(
                    level is TrustLevel.L1
                    and policy.auto_verify
                    and field_type in policy.field_types
                ),
            )
        )

    if not any(item.auto_verified or item.reopened for item in decisions):
        return ConsensusResult(
            CONSENSUS_VERSION,
            tuple(decisions),
            document,
            False,
            unpaired,
            MappingProxyType(_vision_hints(vision)),
        )

    header_updates: dict[str, object] = {}
    line_updates: dict[int, dict[str, object]] = {}
    auto_header: set[str] = set()
    reopened_header: set[str] = set()
    auto_lines: set[int] = set()
    reopened_lines: set[int] = set()
    for decision in decisions:
        if not (decision.auto_verified or decision.reopened):
            continue
        field_value = _field_value(document, decision.key)
        if field_value is None:
            continue
        confidence = (
            Decimal("0.98")
            if decision.auto_verified
            else (
                min(field_value.confidence, REOPEN_CONFIDENCE)
                if field_value.confidence is not None
                else None
            )
        )
        update = field_value.model_copy(update={"confidence": confidence})
        if decision.key.scope == "header":
            header_updates[decision.key.name] = update
            (auto_header if decision.auto_verified else reopened_header).add(decision.key.name)
        else:
            line_updates.setdefault(decision.key.line_no, {})[decision.key.name] = update
            (auto_lines if decision.auto_verified else reopened_lines).add(decision.key.line_no)

    header = document.header.model_copy(update=header_updates)
    lines = tuple(
        line.model_copy(update=line_updates.get(line.line_no, {})) for line in document.lines
    )
    listed_raw = header.extras.get("review_fields", [])
    listed = [str(item) for item in listed_raw] if isinstance(listed_raw, (list, tuple)) else []
    resolved = set(auto_header)
    if "vendor_gstin" in auto_header:
        resolved.add("vendor")
    line_by_no = {line.line_no: line for line in lines}
    resolved.update(
        f"line {line_no}"
        for line_no in auto_lines
        if line_no in line_by_no and not _line_needs_review(line_by_no[line_no])
    )
    review = [item for item in listed if item not in resolved]
    review.extend(name for name in sorted(reopened_header) if name not in review)
    review.extend(
        f"line {line_no}" for line_no in sorted(reopened_lines) if f"line {line_no}" not in review
    )
    other_reason = any(code is not ReasonCode.LOW_CONFIDENCE_DATA for code in document.reason_codes)
    needs = (
        bool(review)
        or other_reason
        or (document.needs_verification and not listed)
        or split_unconfirmed(document)
    )
    reason_codes = document.reason_codes if needs else ()
    if needs and not reason_codes:
        reason_codes = (ReasonCode.LOW_CONFIDENCE_DATA,)
    extras = {
        **header.extras,
        "review_fields": review,
        "consensus": {
            "version": CONSENSUS_VERSION,
            "auto_verified": [item.key.text() for item in decisions if item.auto_verified],
            "reopened": [item.key.text() for item in decisions if item.reopened],
        },
    }
    updated_header = header.model_copy(update={"extras": extras})
    updated = document.model_copy(
        update={
            "header": updated_header,
            "lines": lines,
            "needs_verification": needs,
            "reason_codes": reason_codes,
        }
    )
    return ConsensusResult(
        CONSENSUS_VERSION,
        tuple(decisions),
        updated,
        True,
        unpaired,
        MappingProxyType(_vision_hints(vision)),
    )


def _vision_hints(vision: VisionCheck | None) -> dict[str, tuple[str, ...]]:
    annotations = tuple(
        item.text
        for item in (vision.annotations if vision else ())
        if item.looks_like in {"po_number", "grn_number", "invoice_number"}
    )
    marketplace = tuple(
        dict.fromkeys(
            str(item.value)
            for item in (vision.fields if vision else ())
            if item.name == "marketplace_order_ref"
        )
    )
    page_kinds = tuple(f"{item.page}:{item.kind}" for item in (vision.page_kinds if vision else ()))
    return {
        "annotation_refs": annotations,
        "marketplace_order_refs": marketplace,
        "page_kinds": page_kinds,
    }


def crop_needs(result: ConsensusResult) -> tuple[CropNeed, ...]:
    needs: list[CropNeed] = []
    for decision in result.decisions:
        if decision.rule not in {
            DecisionRule.D42,
            DecisionRule.CONFLICT,
            DecisionRule.REOPEN,
        }:
            continue
        vision = next(
            (
                item
                for item in decision.readings
                if item.reader is ReaderKind.VISION and item.page is not None
            ),
            None,
        )
        field_value = _field_value(result.document, decision.key)
        if vision is None or vision.page is None or vision.box is None or field_value is None:
            continue
        kind = (
            VALUE_KINDS.get(decision.key.name, "")
            if decision.key.scope == "header"
            else LINE_VALUE_KINDS.get(decision.key.name, "")
        )
        if decision.reason not in {
            "crop_missing",
            "validator_failed",
            "hard_validator",
            "readers_disagree",
            "vision_disagrees_with_verified",
        }:
            continue
        mode: Literal["line", "block"] = (
            "block"
            if decision.key.scope == "line"
            or decision.key.name
            in {"taxable_total", "cgst", "sgst", "igst", "round_off", "grand_total"}
            else "line"
        )
        needs.append(
            CropNeed(
                decision.key,
                kind,
                _as_consensus(field_value.value),
                vision.page,
                vision.box,
                mode,
            )
        )
    return tuple(needs)


__all__ = [
    "CONSENSUS_VERSION",
    "DEFAULT_DATE_FUTURE_DAYS",
    "DEFAULT_DATE_PAST_DAYS",
    "HARD_VALIDATORS",
    "HEADER_FIELD_TYPES",
    "LINE_FIELD_TYPES",
    "LINE_VALUE_KINDS",
    "NEVER_AUTOMATIC_HEADER",
    "NEVER_AUTOMATIC_LINE",
    "REOPEN_CONFIDENCE",
    "VALUE_KINDS",
    "ConsensusPolicy",
    "ConsensusResult",
    "ConsensusValue",
    "CropCheck",
    "CropNeed",
    "CropOutcome",
    "DecisionRule",
    "FieldDecision",
    "FieldKey",
    "FieldType",
    "ReaderKind",
    "Reading",
    "TrustLevel",
    "crop_needs",
    "crop_outcome",
    "decide",
    "field_validators",
    "rules_readings",
    "values_agree",
    "vision_readings",
]
