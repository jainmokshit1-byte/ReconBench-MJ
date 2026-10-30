"""Merge the AI reading of a scanned invoice with its rules reading (PD-2e, policy ``arb.llm``).

With the switch on AI the reader leads (D103, DEP-5): its checked value becomes the
field's value and the rules (OCR) reading of the same file stays beside it as a visible
cross-check. A disagreement is flagged ``LLM_DISAGREEMENT`` with both values; the OCR
value is never substituted for a value the AI read. Every AI value enters with origin AI,
unverified and below the review floor, so nothing here is accepted without a person.

Guards (accuracy plan §4.10): a value that failed a standalone check or was picked among
conflicting pages is a suggestion only and never leads (MG-3); a value from a page of
another document is never taken (MG-11); a value a person typed or verified is never
replaced (MG-7); AI confidence is capped below the review floor (MG-2); the document
always needs a person's check (MG-4, MG-10); a ``po_refs`` set gathered from several
pages is flagged (MG-5). HSN/SAC values read by AI must have a valid shape (WI-V1).
Pure: no I/O, no clock, Decimal only.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from reconbench_core.extraction.consensus import LINE_VALUE_KINDS, VALUE_KINDS, values_agree
from reconbench_core.extraction.vision import (
    VISION_HEADER_FIELDS,
    VISION_REF_FIELDS,
    VISION_TYPE_FIELDS,
    AcceptedCell,
    AcceptedField,
    AcceptedLine,
    VisionCheck,
    align_lines,
    vision_document,
)
from reconbench_core.parsers.types import BuyerEntityHint
from reconbench_core.schema import (
    DocLine,
    DocType,
    Document,
    FieldOrigin,
    FieldSource,
    FieldValue,
    FieldVerification,
    ReasonCode,
)

ARB_LLM = "arb.llm"
LLM_DISAGREEMENT = "LLM_DISAGREEMENT"
CROSS_CHECK_KEY = "cross_check"
"""``header.extras`` key: one entry per field the merge compared."""
OCR_READING_KEY = "ocr_reading"
"""``header.extras`` key: the OCR reading of each header field, as text (PD-2h guard)."""

Outcome = Literal["agree", "disagree", "ai_only", "ocr_kept", "person_kept", "ocr_only_line"]

_HSN_SHAPE = re.compile(r"\d{4}|\d{6}|\d{8}")
# Page kinds that belong to another document (MG-11; the S05 page-3 case).
_FOREIGN_PAGES: Mapping[DocType, frozenset[str]] = {
    DocType.INVOICE: frozenset(
        {
            "credit_note",
            "debit_note",
            "purchase_order",
            "goods_receipt",
            "delivery_challan",
            "eway_bill",
        }
    ),
    DocType.PO: frozenset({"invoice", "credit_note", "debit_note", "goods_receipt", "eway_bill"}),
    DocType.GRN: frozenset({"invoice", "credit_note", "debit_note", "purchase_order", "eway_bill"}),
}
_NOT_FIELDS = frozenset({"doc_type", "extras", "line_no"})


@dataclass(frozen=True, slots=True)
class MergePolicy:
    """Stored as data so a change is visible and versioned (accuracy plan §4.3)."""

    name: str = ARB_LLM
    ai_confidence_cap: Decimal = Decimal("0.69")
    """MG-2: below the 0.70 review floor; only a person's check makes the value count."""


POLICY_ARB_LLM = MergePolicy()


@dataclass(frozen=True, slots=True)
class CrossCheck:
    field: str
    """``header:<name>`` or ``line:<n>:<name>`` (line numbers of the merged version)."""
    outcome: Outcome
    ai: str | None
    ocr: str | None
    reason: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "field": self.field,
            "outcome": self.outcome,
            "ai": self.ai,
            "ocr": self.ocr,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class MergeResult:
    policy: str
    document: Document
    cross_checks: tuple[CrossCheck, ...]
    left_out: Mapping[str, int]
    """Values of the answer that were not used, by reason (suggestions only)."""

    @property
    def usable(self) -> bool:
        """Whether the AI read at least one value that the merged version carries."""

        return any(item.outcome in {"agree", "disagree", "ai_only"} for item in self.cross_checks)

    @property
    def disagreements(self) -> tuple[CrossCheck, ...]:
        return tuple(item for item in self.cross_checks if item.outcome == "disagree")


def hsn_shape_ok(text: str) -> bool:
    """V-HSN (WI-V1): a 4-, 6- or 8-digit HSN code, or a 6-digit SAC code."""

    return _HSN_SHAPE.fullmatch("".join(text.split())) is not None


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        return ";".join(str(item) for item in value)
    if isinstance(value, date):
        return value.isoformat()
    return str(getattr(value, "value", value))


def _plain(field: FieldValue[Any] | None) -> Any:
    return field.value if field is not None else None


def _held_by_person(field: FieldValue[Any] | None) -> bool:
    return field is not None and (
        field.source is FieldSource.MANUAL
        or field.origin is FieldOrigin.MANUAL
        or field.verification is FieldVerification.USER
    )


def _usable_reading(
    check: VisionCheck, doc_type: DocType
) -> tuple[VisionCheck, dict[str, str], Counter[str]]:
    """The answer without values that may only be suggestions, and why each one left."""

    foreign = {
        item.page for item in check.page_kinds if item.kind in _FOREIGN_PAGES.get(doc_type, ())
    }
    reasons: dict[str, str] = {}
    left_out: Counter[str] = Counter()

    def why(item: AcceptedField | AcceptedLine) -> str | None:
        if item.page in foreign:
            return "other_document_page"
        if "page_conflict" in item.check_failed:
            return "page_conflict"
        if item.check_failed:
            return "check_failed:" + ",".join(sorted(item.check_failed))
        return None

    fields: list[AcceptedField] = []
    for field_item in check.fields:
        reason = why(field_item)
        if reason is None:
            fields.append(field_item)
        else:
            reasons.setdefault(field_item.name, reason)
            left_out[reason.split(":")[0]] += 1
    lines: list[AcceptedLine] = []
    for line in check.lines:
        reason = why(line)
        if reason is not None:
            left_out[reason.split(":")[0]] += 1
            continue
        cells: list[AcceptedCell] = []
        for cell in line.cells:
            if cell.name == "hsn_sac" and not hsn_shape_ok(cell.text):
                left_out["hsn_shape"] += 1
                continue
            cells.append(cell)
        lines.append(replace(line, index=len(lines) + 1, cells=tuple(cells)))
    return replace(check, fields=tuple(fields), lines=tuple(lines)), reasons, left_out


def _from_ocr(field: FieldValue[Any]) -> FieldValue[Any]:
    """A rules value kept in the AI version says it was read by OCR, never by AI."""

    if field.origin is None and field.source is FieldSource.RULE:
        return field.model_copy(update={"origin": FieldOrigin.OCR})
    return field


def _from_ai(field: FieldValue[Any], policy: MergePolicy) -> FieldValue[Any]:
    confidence = field.confidence if field.confidence is not None else policy.ai_confidence_cap
    return field.model_copy(
        update={
            "confidence": min(confidence, policy.ai_confidence_cap),
            "source": FieldSource.AI,
            "origin": FieldOrigin.AI,
            "verification": FieldVerification.NONE,
            "verified_by": None,
            "verified_at": None,
        }
    )


def _merge_field(
    key: str,
    kind: str,
    ocr: FieldValue[Any] | None,
    ai: FieldValue[Any] | None,
    policy: MergePolicy,
    *,
    left_out_reason: str | None = None,
) -> tuple[FieldValue[Any] | None, CrossCheck | None]:
    if ocr is None and ai is None:
        return None, None
    ocr_text, ai_text = _text(_plain(ocr)), _text(_plain(ai))
    if _held_by_person(ocr):
        # MG-7: a value a person typed or verified stays; the AI value is shown only.
        return ocr, CrossCheck(key, "person_kept", ai_text, ocr_text)
    if ai is None:
        assert ocr is not None
        return _from_ocr(ocr), CrossCheck(
            key, "ocr_kept", None, ocr_text, left_out_reason or "not_read"
        )
    merged = _from_ai(ai, policy)
    if ocr is None or not ocr_text:
        return merged, CrossCheck(key, "ai_only", ai_text, None)
    if values_agree(kind, _plain(ocr), _plain(ai)):
        return merged, CrossCheck(key, "agree", ai_text, ocr_text)
    return merged, CrossCheck(key, "disagree", ai_text, ocr_text, LLM_DISAGREEMENT)


def _line_rows(document: Document) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in document.lines:
        quantity = _plain(line.quantity) or _plain(line.received_qty) or _plain(line.accepted_qty)
        rows.append(
            {
                "line_no": line.line_no,
                "amount": _plain(line.amount),
                "quantity": quantity,
                "rate": _plain(line.rate),
                "description": _plain(line.description) or "",
            }
        )
    return rows


def _merge_lines(
    ocr: Document, ai: Document, check: VisionCheck, policy: MergePolicy
) -> tuple[tuple[DocLine, ...], list[CrossCheck]]:
    if not ai.lines:
        return tuple(
            line.model_copy(
                update={
                    name: _from_ocr(value)
                    for name in DocLine.model_fields
                    if isinstance(value := getattr(line, name), FieldValue)
                }
            )
            for line in ocr.lines
        ), []
    paired = align_lines(_line_rows(ocr), check.lines)
    ocr_by_no = {line.line_no: line for line in ocr.lines}
    names = [name for name in DocLine.model_fields if name not in _NOT_FIELDS]
    lines: list[DocLine] = []
    checks: list[CrossCheck] = []
    for ai_line, ocr_no in zip(ai.lines, paired, strict=True):
        ocr_line = ocr_by_no.get(ocr_no) if ocr_no is not None else None
        update: dict[str, Any] = {}
        for name in names:
            ocr_value = getattr(ocr_line, name, None) if ocr_line is not None else None
            value, cross = _merge_field(
                f"line:{ai_line.line_no}:{name}",
                LINE_VALUE_KINDS.get(name, "text"),
                ocr_value,
                getattr(ai_line, name),
                policy,
            )
            update[name] = value
            if cross is not None:
                checks.append(cross)
        extras = {**(ocr_line.extras if ocr_line is not None else {}), **ai_line.extras}
        if ocr_no is not None:
            extras["ocr_line_no"] = ocr_no
        lines.append(ai_line.model_copy(update={**update, "extras": extras}))
    for ocr_no in sorted(set(ocr_by_no) - {no for no in paired if no is not None}):
        # A line only the rules read is not carried; the person sees it here.
        checks.append(
            CrossCheck(
                f"line:{ocr_no}:*",
                "ocr_only_line",
                None,
                _text(_plain(ocr_by_no[ocr_no].amount)),
                "line_not_read_by_ai",
            )
        )
    return tuple(lines), checks


def merge_readings(
    ocr: Document,
    check: VisionCheck,
    policy: MergePolicy = POLICY_ARB_LLM,
    *,
    buyer_entities: Sequence[BuyerEntityHint] = (),
    gst_rates: Sequence[Decimal] | None = None,
) -> MergeResult:
    """The candidate AI version of ``ocr`` (the rules reading of the same file)."""

    doc_type = ocr.header.doc_type
    usable, reasons, left_out = _usable_reading(check, doc_type)
    ai = vision_document(usable, doc_type, buyer_entities=buyer_entities, gst_rates=gst_rates)
    # The reader's field names for each model field, to say why a value was left out.
    reader_names: dict[str, list[str]] = {}
    for mapping in (
        VISION_HEADER_FIELDS,
        VISION_TYPE_FIELDS[doc_type],
        VISION_REF_FIELDS[doc_type],
    ):
        for source, target in mapping.items():
            reader_names.setdefault(target, []).append(source)
    cross_checks: list[CrossCheck] = []
    header_update: dict[str, Any] = {}
    for name in type(ocr.header).model_fields:
        if name in _NOT_FIELDS:
            continue
        reason = next(
            (reasons[source] for source in reader_names.get(name, ()) if source in reasons), None
        )
        value, cross = _merge_field(
            f"header:{name}",
            VALUE_KINDS.get(name, "text"),
            getattr(ocr.header, name),
            getattr(ai.header, name, None),
            policy,
            left_out_reason=reason,
        )
        if name == "po_refs" and cross is not None and cross.outcome != "person_kept":
            pages = {item.page for item in usable.fields if item.name == "po_ref"}
            if len(pages) > 1:
                # MG-5: a reference set gathered from several pages is never trusted alone.
                cross = replace(
                    cross, reason=";".join(filter(None, [cross.reason, "refs_from_several_pages"]))
                )
        header_update[name] = value
        if cross is not None:
            cross_checks.append(cross)
    lines, line_checks = _merge_lines(ocr, ai, usable, policy)
    cross_checks.extend(line_checks)

    extras = {
        **ocr.header.extras,
        **ai.header.extras,
        "reading_policy": policy.name,
        CROSS_CHECK_KEY: [item.as_dict() for item in cross_checks],
        OCR_READING_KEY: {
            name: text
            for name in type(ocr.header).model_fields
            if name not in _NOT_FIELDS
            and (text := _text(_plain(getattr(ocr.header, name)))) is not None
        },
    }
    header = ocr.header.model_copy(update={**header_update, "extras": extras})
    read_something = any(item.outcome in {"agree", "disagree", "ai_only"} for item in cross_checks)
    reasons_out = set(ocr.reason_codes)
    if read_something:
        # The AI read the page, so it is no longer unreadable; it still needs a person.
        reasons_out.discard(ReasonCode.NO_TEXT_LAYER)
        reasons_out.add(ReasonCode.LOW_CONFIDENCE_DATA)
    document = ocr.model_copy(
        update={
            "header": header,
            "lines": lines,
            "needs_verification": True,
            "reason_codes": tuple(sorted(reasons_out, key=lambda code: code.value)),
        }
    )
    return MergeResult(policy.name, document, tuple(cross_checks), dict(left_out))


__all__ = [
    "ARB_LLM",
    "CROSS_CHECK_KEY",
    "LLM_DISAGREEMENT",
    "OCR_READING_KEY",
    "POLICY_ARB_LLM",
    "CrossCheck",
    "MergePolicy",
    "MergeResult",
    "hsn_shape_ok",
    "merge_readings",
]
