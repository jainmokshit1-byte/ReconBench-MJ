"""Pure, deterministic PO linking through the five-step anchor ladder."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any, Self
from uuid import UUID

from pydantic import Field, model_validator

from reconbench_core.align import (
    AlignmentConfig,
    AlignmentWeights,
    align_lines,
)
from reconbench_core.extraction.handwritten_refs import short_of
from reconbench_core.extraction.pages import split_unconfirmed
from reconbench_core.normalize import canonicalize_document_number, parse_decimal
from reconbench_core.schema import (
    DecimalValue,
    DocLine,
    DocType,
    Document,
    FieldOrigin,
    FieldVerification,
    FrozenModel,
    GRNHeader,
    InvoiceHeader,
    LinkLevel,
    LinkMethod,
    LinkState,
    LinkType,
    POHeader,
    POStatus,
    ReasonCode,
    ResolutionOutcome,
    ToleranceMode,
)

_ONE = Decimal("1")
_ZERO = Decimal("0")
_FOUR_PLACES = Decimal("0.0001")
MANUAL_SELECTION_EVIDENCE = "Manually selected by the operator"
OPENING_SUPPORT = UUID(int=0)
"""Key of opening-balance receipts in ``MappingPO.grn_support_by_source``."""
_HANDWRITTEN_AMOUNT_TOLERANCE = Decimal("1")
"""Rupees: a typed handwritten reference needs the invoice total within this of the
target's total (WI-D4, owner 21 Oct)."""
_REFERENCE_WEIGHT = Decimal("0.40")
_PART_WEIGHT = Decimal("0.35")
_AMOUNT_WEIGHT = Decimal("0.25")


def _default_alignment() -> AlignmentConfig:
    return AlignmentConfig(
        weights=AlignmentWeights(
            desc=Decimal("0.55"),
            code=Decimal("0.20"),
            hsn=Decimal("0.15"),
            uom=Decimal("0.10"),
        ),
        item_aligned=Decimal("0.80"),
        item_review=Decimal("0.60"),
        item_single_line_explicit=Decimal("0.50"),
        spec_conflict_cap=Decimal("0.60"),
    )


class MappingConfig(FrozenModel):
    """Link thresholds and configured confidences from the active engine config."""

    link_auto_accept: DecimalValue = Decimal("0.95")
    confidence_labeled: DecimalValue = Decimal("0.99")
    confidence_reverse: DecimalValue = Decimal("0.97")
    confidence_via_grn: DecimalValue = Decimal("0.95")
    confidence_manual: DecimalValue = Decimal("1.00")
    reverse_lookup_window_runs: int = Field(default=6, ge=1, le=6)
    step3_min_score: DecimalValue = Decimal("0.80")
    step3_min_lead: DecimalValue = Decimal("0.15")
    amount_tolerance_pct: DecimalValue = Decimal("1")
    amount_tolerance_abs: DecimalValue = Decimal("10")
    rate_tolerance_pct: DecimalValue = Decimal("0.5")
    rate_tolerance_abs: DecimalValue = Decimal("1")
    quantity_tolerance_pct: DecimalValue = Decimal("0")
    quantity_tolerance_abs: DecimalValue = Decimal("0")
    limit_mode: ToleranceMode = ToleranceMode.OR
    reference_system_check_min_confidence: DecimalValue = Decimal("0.85")
    date_normal_days: int = Field(default=7, ge=0)
    date_review_days: int = Field(default=15, ge=0)
    alignment: AlignmentConfig = Field(default_factory=_default_alignment)

    @model_validator(mode="after")
    def _validate_thresholds(self) -> Self:
        if not Decimal("0") <= self.link_auto_accept <= Decimal("1"):
            raise ValueError("link auto-accept threshold must be between 0 and 1")
        if not Decimal("0") <= self.step3_min_score <= Decimal("0.90"):
            raise ValueError("content threshold must be between 0 and 0.90")
        if not Decimal("0") <= self.step3_min_lead <= Decimal("1"):
            raise ValueError("content lead threshold must be between 0 and 1")
        if (
            min(
                self.amount_tolerance_pct,
                self.amount_tolerance_abs,
                self.rate_tolerance_pct,
                self.rate_tolerance_abs,
                self.quantity_tolerance_pct,
                self.quantity_tolerance_abs,
            )
            < 0
        ):
            raise ValueError("mapping tolerances must be non-negative")
        if not Decimal("0") <= self.reference_system_check_min_confidence <= Decimal("1"):
            raise ValueError("reference verification confidence must be between 0 and 1")
        if self.date_review_days < self.date_normal_days:
            raise ValueError("date review days must not precede normal days")
        return self


class MappingDocument(FrozenModel):
    """One persisted invoice or GRN plus mapping context and prior decisions."""

    document_id: UUID
    natural_key: str = ""
    vendor_id: UUID | None = None
    document: Document
    source_text: str | None = None
    manual_po_ids: tuple[UUID, ...] = ()
    rejected_po_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def _supported_document_type(self) -> Self:
        if self.document.header.doc_type not in {DocType.INVOICE, DocType.GRN}:
            raise ValueError("mapping source documents must be invoices or GRNs")
        return self


class MappingPO(FrozenModel):
    """A PO plus the phase-A remaining quantity/value snapshot for content scoring."""

    document_id: UUID
    vendor_id: UUID | None = None
    document: Document
    open_balance: DecimalValue = Decimal("0")
    open_qty_by_line: dict[int, DecimalValue] = Field(default_factory=dict)
    grn_support_qty_by_line: dict[int, DecimalValue] = Field(default_factory=dict)
    open_grn_qty_by_line: dict[int, DecimalValue] = Field(default_factory=dict)
    grn_support_by_source: dict[UUID, dict[int, DecimalValue]] | None = None
    """Accepted receipt quantity per PO line, per source document, from links that are
    already auto or confirmed, plus opening balances under :data:`OPENING_SUPPORT`
    (WI-D4, D90: no self-support). ``None`` is the older snapshot, whose
    ``grn_support_qty_by_line`` is used as given."""

    @model_validator(mode="after")
    def _purchase_order_document(self) -> Self:
        if self.document.header.doc_type is not DocType.PO:
            raise ValueError("mapping PO records must contain a PO document")
        return self


class LinkCandidate(FrozenModel):
    """A top-ranked PO candidate shown when no deterministic anchor resolves."""

    po_document_id: UUID
    po_no: str
    method: LinkMethod = LinkMethod.CONTENT
    score: DecimalValue
    evidence: str
    line_no: int | None = Field(default=None, ge=1)
    signals: dict[str, DecimalValue] = Field(default_factory=dict)
    missing_signals: tuple[str, ...] = ()
    rejection_reasons: tuple[str, ...] = ()


class FieldVerificationUpdate(FrozenModel):
    """A pure verification or revocation result for the adapter to persist."""

    document_id: UUID
    field_name: str
    matched_document_id: UUID | None = None
    value_text: str
    line_no: int | None = Field(default=None, ge=1)
    verification: FieldVerification = FieldVerification.SYSTEM_CHECK


class ProposedLink(FrozenModel):
    """An immutable proposed invoice/GRN to PO link."""

    from_document_id: UUID
    to_document_id: UUID
    link_type: LinkType
    level: LinkLevel
    method: LinkMethod
    confidence: DecimalValue
    state: LinkState
    evidence: str
    line_no: int | None = Field(default=None, ge=1)
    via_document_id: UUID | None = None


class LinkResolution(FrozenModel):
    """Chosen links or the best candidates and stable reason for no link."""

    document_id: UUID
    outcome: ResolutionOutcome
    links: tuple[ProposedLink, ...] = ()
    candidates: tuple[LinkCandidate, ...] = ()
    reason_code: ReasonCode | None = None
    reason_detail: str | None = None
    reference_text: str | None = None
    field_verifications: tuple[FieldVerificationUpdate, ...] = ()


ContentDetail = tuple[
    Decimal,
    tuple[tuple[int, int, Decimal], ...],
    dict[str, Decimal],
    tuple[str, ...],
    tuple[str, ...],
]
LineCandidate = tuple[
    MappingPO,
    Decimal,
    int,
    Decimal,
    dict[str, Decimal],
    tuple[str, ...],
    tuple[str, ...],
]
AssignedCandidate = tuple[
    MappingPO,
    int,
    Decimal,
    Decimal,
    dict[str, Decimal],
    tuple[str, ...],
    tuple[str, ...],
]


def _doc_no(document: Document) -> str | None:
    field = document.header.doc_no
    return field.value if field is not None else None


def _canonical(value: str | None) -> str:
    return _canonical_text(value or "")


@lru_cache(maxsize=65_536)
def _canonical_text(value: str) -> str:
    # A pure function of the text: memoised because reverse matching compares every
    # window of a document's text with every PO number (WI-PERF1, D98).
    return canonicalize_document_number(value)


def _last_numeric_run(value: str) -> str | None:
    matches = re.findall(r"\d+", value)
    return matches[-1].lstrip("0") or "0" if matches else None


@lru_cache(maxsize=65_536)
def _without_leading_label(value: str) -> str:
    return re.sub(r"^(?:PO|ORDER|PUR)", "", _canonical(value))


def _po_index(purchase_orders: tuple[MappingPO, ...]) -> dict[str, tuple[MappingPO, ...]]:
    """POs by canonical number, each list in the snapshot's order (WI-PERF1, D98)."""

    index: dict[str, list[MappingPO]] = {}
    for po in purchase_orders:
        index.setdefault(_canonical(_doc_no(po.document)), []).append(po)
    return {number: tuple(pos) for number, pos in index.items()}


def _po_matches(
    reference: str,
    purchase_orders: tuple[MappingPO, ...],
    vendor_id: UUID | None,
    index: Mapping[str, tuple[MappingPO, ...]] | None = None,
) -> tuple[tuple[MappingPO, ...], bool]:
    canonical_reference = _canonical(reference)
    if index is not None:
        exact = index.get(canonical_reference, ())
    else:
        exact = tuple(
            po for po in purchase_orders if _canonical(_doc_no(po.document)) == canonical_reference
        )
    if exact:
        if vendor_id is None:
            # Document numbers are scoped to suppliers in real purchasing
            # systems. A globally unique candidate in today's upload pool is
            # not proof that an unassigned invoice belongs to that supplier.
            return (), True
        same_vendor = tuple(po for po in exact if po.vendor_id == vendor_id)
        if len(same_vendor) == 1:
            return same_vendor, False
        return (), bool(not same_vendor)
    if vendor_id is None:
        return (), False

    same_vendor_pos = tuple(po for po in purchase_orders if po.vendor_id == vendor_id)
    stripped = _without_leading_label(reference)
    prefix_matches = tuple(
        po
        for po in same_vendor_pos
        if stripped and _without_leading_label(_doc_no(po.document) or "") == stripped
    )
    if len(prefix_matches) == 1:
        return prefix_matches, False
    if len(prefix_matches) > 1:
        return (), False

    reference_tail = _last_numeric_run(reference)
    if reference_tail is None or len(re.findall(r"\d+", reference)[-1]) < 5:
        return (), False
    tail_matches = tuple(
        po
        for po in same_vendor_pos
        if (po_no := _doc_no(po.document)) is not None
        and _last_numeric_run(po_no) == reference_tail
    )
    return (tail_matches, False) if len(tail_matches) == 1 else ((), False)


def _header_refs(document: Document) -> tuple[tuple[str, ...], str | None, str | None]:
    header = document.header
    if isinstance(header, InvoiceHeader):
        po_refs = header.po_refs.value if header.po_refs else ()
        return tuple(po_refs), header.challan_no.value if header.challan_no else None, None
    if isinstance(header, GRNHeader):
        po_refs = header.po_refs.value if header.po_refs else ()
        return tuple(po_refs), header.challan_no.value if header.challan_no else None, None
    return (), None, None


def _line_po_refs(document: Document) -> tuple[tuple[int, str], ...]:
    return tuple(
        (line.line_no, line.po_ref.value) for line in document.lines if line.po_ref is not None
    )


def _field_is_user_verified(field: object | None) -> bool:
    return bool(
        field is not None
        and getattr(field, "verification", FieldVerification.NONE) is FieldVerification.USER
    )


def _field_is_verified(field: object | None) -> bool:
    return bool(
        field is not None
        and getattr(field, "verification", FieldVerification.NONE)
        in {FieldVerification.USER, FieldVerification.SYSTEM_CHECK}
    )


def _po_is_open(po: MappingPO) -> bool:
    status = po.document.header.status if isinstance(po.document.header, POHeader) else None
    return (
        po.open_balance > _ZERO
        and any(quantity > _ZERO for quantity in po.open_qty_by_line.values())
        and (status is None or status.value is POStatus.OPEN)
    )


def _unique_header_po_refs(document: Document) -> tuple[str, ...]:
    references, _, _ = _header_refs(document)
    unique: dict[str, str] = {}
    for reference in references:
        unique.setdefault(_canonical(reference), reference)
    return tuple(unique.values())


def _confidence_state(confidence: Decimal, config: MappingConfig) -> LinkState:
    return LinkState.AUTO if confidence >= config.link_auto_accept else LinkState.SUGGESTED


def _link_type(source: MappingDocument) -> LinkType:
    return (
        LinkType.INV_PO if source.document.header.doc_type is DocType.INVOICE else LinkType.GRN_PO
    )


def _links_for_matches(
    source: MappingDocument,
    matches: tuple[tuple[MappingPO, LinkLevel, int | None, str], ...],
    *,
    method: LinkMethod,
    confidence: Decimal,
    config: MappingConfig,
    via_document_id: UUID | None = None,
    held: bool = False,
    force_suggested: bool = False,
) -> tuple[ProposedLink, ...]:
    """Proposed links; ``held`` keeps them suggestions (an unconfirmed page split on the path)."""

    unique: dict[tuple[UUID, int | None], ProposedLink] = {}
    for po, level, line_no, evidence in matches:
        key = (po.document_id, line_no)
        state = (
            LinkState.CONFIRMED
            if method is LinkMethod.MANUAL
            else _confidence_state(confidence, config)
        )
        # A document split out of a bundle is not linked automatically until a
        # person accepts the split (accuracy plan AC-3).
        if state is LinkState.AUTO and (
            force_suggested
            or held
            or split_unconfirmed(source.document)
            or split_unconfirmed(po.document)
        ):
            state = LinkState.SUGGESTED
            if force_suggested:
                evidence += "; verification required"
            if held or split_unconfirmed(source.document) or split_unconfirmed(po.document):
                evidence += "; file split not confirmed"
        unique[key] = ProposedLink(
            from_document_id=source.document_id,
            to_document_id=po.document_id,
            link_type=_link_type(source),
            level=level,
            method=method,
            confidence=confidence,
            state=state,
            evidence=evidence,
            line_no=line_no,
            via_document_id=via_document_id,
        )
    return tuple(
        sorted(unique.values(), key=lambda link: (str(link.to_document_id), link.line_no or 0))
    )


def _labeled_resolution(
    source: MappingDocument,
    purchase_orders: tuple[MappingPO, ...],
    config: MappingConfig,
) -> tuple[tuple[ProposedLink, ...], ReasonCode | None, str | None]:
    header_refs = _unique_header_po_refs(source.document)
    line_refs = _line_po_refs(source.document)
    header_ref_field = getattr(source.document.header, "po_refs", None)
    header_verified = _field_is_verified(header_ref_field)
    multi_po_header = len(header_refs) > 1
    links: list[ProposedLink] = []
    missing: list[str] = []
    mismatched: list[str] = []

    # More than one header PO is a candidate set for line-level content matching,
    # not permission to attach every invoice line to every PO.
    if multi_po_header:
        for reference in header_refs:
            matches, vendor_mismatch = _po_matches(reference, purchase_orders, source.vendor_id)
            if vendor_mismatch:
                mismatched.append(reference)
            elif not matches:
                missing.append(reference)
        if mismatched:
            return (), ReasonCode.VENDOR_MISMATCH, mismatched[0]
        if missing and header_verified:
            return (), ReasonCode.NO_PO_LINK, missing[0]
    elif header_refs:
        reference = header_refs[0]
        matches, vendor_mismatch = _po_matches(reference, purchase_orders, source.vendor_id)
        if vendor_mismatch:
            return (), ReasonCode.VENDOR_MISMATCH, reference
        eligible = tuple(po for po in matches if po.document_id not in source.rejected_po_ids)
        if not matches and header_verified:
            return (), ReasonCode.NO_PO_LINK, reference
        if eligible and header_verified and source.document.header.doc_type is DocType.INVOICE:
            explicitly_labeled_lines = {line_no for line_no, _ in line_refs}
            inherited_matches = tuple(
                (
                    po,
                    LinkLevel.LINE,
                    line.line_no,
                    f"Header PO reference inherited by line: {reference}",
                )
                for line in source.document.lines
                if line.line_no not in explicitly_labeled_lines
                for po in eligible
            )
            if inherited_matches:
                links.extend(
                    _links_for_matches(
                        source,
                        inherited_matches,
                        method=LinkMethod.LABELED,
                        confidence=config.confidence_labeled,
                        config=config,
                    )
                )
        elif eligible and header_verified:
            links.extend(
                _links_for_matches(
                    source,
                    tuple(
                        (po, LinkLevel.HEADER, None, f"Labeled PO reference: {reference}")
                        for po in eligible
                    ),
                    method=LinkMethod.LABELED,
                    confidence=config.confidence_labeled,
                    config=config,
                )
            )

    for line_no, reference in line_refs:
        line_field = next(
            (
                line.po_ref
                for line in source.document.lines
                if line.line_no == line_no and line.po_ref is not None
            ),
            None,
        )
        line_verified = _field_is_verified(line_field)
        matches, vendor_mismatch = _po_matches(reference, purchase_orders, source.vendor_id)
        if vendor_mismatch:
            mismatched.append(reference)
            continue
        eligible = tuple(po for po in matches if po.document_id not in source.rejected_po_ids)
        if not eligible:
            if not matches and line_verified:
                missing.append(reference)
            continue
        if not line_verified:
            continue
        links.extend(
            _links_for_matches(
                source,
                tuple(
                    (po, LinkLevel.LINE, line_no, f"Labeled line PO reference: {reference}")
                    for po in eligible
                ),
                method=LinkMethod.LABELED,
                confidence=config.confidence_labeled,
                config=config,
            )
        )
    if mismatched:
        return (), ReasonCode.VENDOR_MISMATCH, mismatched[0]
    if missing:
        return (), ReasonCode.NO_PO_LINK, missing[0]
    if links:
        return tuple(links), None, None
    return (), None, None


def _flatten(value: object) -> list[str]:
    if isinstance(value, dict):
        output: list[str] = []
        for key, item in value.items():
            output.append(str(key))
            output.extend(_flatten(item))
        return output
    if isinstance(value, (list, tuple)):
        return [part for item in value for part in _flatten(item)]
    return [str(value)] if value is not None else []


def _source_text(source: MappingDocument) -> str:
    if source.source_text:
        return source.source_text
    if source.document.raw_text:
        return source.document.raw_text
    header = source.document.header
    parts = [
        header.doc_no.value if header.doc_no else "",
        header.vendor_name.value if header.vendor_name else "",
        header.vendor_gstin.value if header.vendor_gstin else "",
        header.e_way_bill_no.value
        if isinstance(header, InvoiceHeader) and header.e_way_bill_no
        else "",
        header.irn.value if isinstance(header, InvoiceHeader) and header.irn else "",
        # Typed handwritten references (WI-Q2) are matched only by type, never by a
        # text scan; their written text is already in the annotations.
        *_flatten({key: value for key, value in header.extras.items() if key != "typed_refs"}),
    ]
    for line in sorted(source.document.lines, key=lambda item: item.line_no):
        parts.extend(
            (
                line.description.value if line.description else "",
                line.item_code.value if line.item_code else "",
                line.hsn_sac.value if line.hsn_sac else "",
                *_flatten(line.extras),
            )
        )
    return " ".join(part for part in parts if part)


def _ignored_reverse_values(source: MappingDocument) -> tuple[set[str], set[Decimal]]:
    header = source.document.header
    ignored: set[str] = set()
    non_po_references = [
        _doc_no(source.document),
        header.vendor_gstin.value if header.vendor_gstin else None,
    ]
    if isinstance(header, InvoiceHeader):
        non_po_references.extend(
            (
                *(header.grn_refs.value if header.grn_refs else ()),
                header.challan_no.value if header.challan_no else None,
                header.e_way_bill_no.value if header.e_way_bill_no else None,
                header.irn.value if header.irn else None,
            )
        )
    elif isinstance(header, GRNHeader):
        non_po_references.extend(
            (
                header.challan_no.value if header.challan_no else None,
                header.supplier_invoice_no.value if header.supplier_invoice_no else None,
            )
        )
    for value in non_po_references:
        if value:
            ignored.add(_canonical(value))
            ignored.update(_canonical(token) for token in re.findall(r"[A-Za-z0-9]+", value))
    annotation_values = _extra_values(
        source.document,
        {
            "annotation_refs",
            "handwritten_refs",
            "handwritten_references",
            "typed_refs",
            "marketplace_order_no",
            "marketplace_order_refs",
            "marketplace_order_ref",
        },
    )
    for value in annotation_values:
        ignored.add(_canonical(value))
        ignored.update(_canonical(token) for token in re.findall(r"[A-Za-z0-9]+", value))
    for line in source.document.lines:
        if line.hsn_sac is not None:
            ignored.add(_canonical(line.hsn_sac.value))
    ignored.discard("")
    amounts: set[Decimal] = set()
    for field in (
        header.taxable_total,
        header.cgst,
        header.sgst,
        header.igst,
        header.grand_total,
    ):
        if field is not None:
            amounts.add(field.value)
    for line in source.document.lines:
        if line.amount is not None:
            amounts.add(line.amount.value)
    return ignored, amounts


def _label_nearby(text: str, start: int) -> bool:
    context = text[max(0, start - 48) : start]
    return bool(
        re.search(
            r"(?:\bPO\b|\bP\.?O\.?\b|\bPURCHASE\s+ORDER\b|\bORDER\b)"
            r"\s*(?:NO\.?|NUMBER|#)?\s*[:#-]?\s*$",
            context,
            flags=re.I,
        )
    )


def _decimal_text(value: str) -> Decimal | None:
    try:
        return parse_decimal(value)
    except ValueError:
        return None


def _reverse_matches(
    source: MappingDocument,
    purchase_orders: tuple[MappingPO, ...],
    config: MappingConfig,
) -> tuple[tuple[ProposedLink, ...], ReasonCode | None, str | None]:
    text = _source_text(source)
    runs = list(re.finditer(r"[A-Za-z0-9]+", text))
    ignored, amounts = _ignored_reverse_values(source)
    index = _po_index(purchase_orders)
    found: dict[UUID, tuple[MappingPO, str]] = {}
    mismatched: list[str] = []
    for start_index in range(len(runs)):
        upper = min(len(runs), start_index + config.reverse_lookup_window_runs)
        for end_index in range(start_index, upper):
            start = runs[start_index].start()
            end = runs[end_index].end()
            reference = text[start:end]
            canonical = _canonical(reference)
            if not canonical or canonical in ignored:
                continue
            parsed_amount = _decimal_text(reference)
            if parsed_amount is not None and parsed_amount in amounts:
                continue
            if len(canonical) < 5 and not _label_nearby(text, start):
                continue
            matches, vendor_mismatch = _po_matches(
                reference, purchase_orders, source.vendor_id, index
            )
            if vendor_mismatch:
                mismatched.append(reference)
                continue
            for po in matches:
                if po.document_id not in source.rejected_po_ids:
                    found.setdefault(po.document_id, (po, reference))
    links = _links_for_matches(
        source,
        tuple(
            (po, LinkLevel.HEADER, None, f"PO number found in document text: {reference}")
            for po, reference in found.values()
        ),
        method=LinkMethod.REVERSE,
        confidence=config.confidence_reverse,
        config=config,
        force_suggested=True,
    )
    if links:
        return links, None, None
    if mismatched:
        return (), ReasonCode.VENDOR_MISMATCH, mismatched[0]
    return (), None, None


def _grn_reference_matches_invoice(invoice: MappingDocument, grn: MappingDocument) -> bool:
    invoice_header = invoice.document.header
    grn_header = grn.document.header
    if not isinstance(invoice_header, InvoiceHeader) or not isinstance(grn_header, GRNHeader):
        return False
    grn_no = _doc_no(grn.document)
    if (
        grn_no
        and invoice_header.grn_refs is not None
        and any(_canonical(value) == _canonical(grn_no) for value in invoice_header.grn_refs.value)
    ):
        return True
    invoice_no = _doc_no(invoice.document)
    supplier_invoice_match = bool(
        invoice_no
        and grn_header.supplier_invoice_no
        and _canonical(invoice_no) == _canonical(grn_header.supplier_invoice_no.value)
    )
    challan_invoice_number_match = bool(
        invoice_no
        and grn_header.challan_no
        and _canonical(invoice_no) == _canonical(grn_header.challan_no.value)
    )
    challan_match = bool(
        invoice_header.challan_no
        and grn_header.challan_no
        and _canonical(invoice_header.challan_no.value) == _canonical(grn_header.challan_no.value)
    )
    return supplier_invoice_match or challan_invoice_number_match or challan_match


def _invoice_grn_identifier_anchor(invoice: MappingDocument, grn: MappingDocument) -> str | None:
    """Return a direct invoice-number/challan anchor, excluding invoice GRN refs.

    An invoice's handwritten GRN number is corroboration only. The printed
    supplier-invoice number or challan relationships are independent anchors
    and may produce suggestions without an amount/part match.
    """

    invoice_header = invoice.document.header
    grn_header = grn.document.header
    if not isinstance(invoice_header, InvoiceHeader) or not isinstance(grn_header, GRNHeader):
        return None
    invoice_no = _doc_no(invoice.document)
    if (
        invoice_no
        and grn_header.supplier_invoice_no is not None
        and _canonical(invoice_no) == _canonical(grn_header.supplier_invoice_no.value)
    ):
        return "supplier_invoice_no"
    if (
        invoice_no
        and grn_header.challan_no is not None
        and _canonical(invoice_no) == _canonical(grn_header.challan_no.value)
    ):
        return "grn_challan_matches_invoice_no"
    if (
        invoice_header.challan_no is not None
        and grn_header.challan_no is not None
        and _canonical(invoice_header.challan_no.value) == _canonical(grn_header.challan_no.value)
    ):
        return "challan_no"
    return None


def _grn_matches_invoice(invoice: MappingDocument, grn: MappingDocument) -> bool:
    """Require a resolved, identical supplier before accepting a GRN reference."""
    if invoice.vendor_id is None or grn.vendor_id is None or invoice.vendor_id != grn.vendor_id:
        return False
    return _grn_reference_matches_invoice(invoice, grn)


def _verified_invoice_grn_anchor(invoice: MappingDocument, grn: MappingDocument) -> bool:
    invoice_header = invoice.document.header
    grn_header = grn.document.header
    if not isinstance(invoice_header, InvoiceHeader) or not isinstance(grn_header, GRNHeader):
        return False
    direct_anchor = _invoice_grn_identifier_anchor(invoice, grn)
    invoice_no = _doc_no(invoice.document)
    if direct_anchor == "supplier_invoice_no":
        return bool(
            invoice_no
            and grn_header.supplier_invoice_no is not None
            and _canonical(invoice_no) == _canonical(grn_header.supplier_invoice_no.value)
            and _field_is_verified(invoice_header.doc_no)
            and _field_is_verified(grn_header.supplier_invoice_no)
        )
    if direct_anchor == "grn_challan_matches_invoice_no":
        return bool(
            invoice_no
            and grn_header.challan_no is not None
            and _canonical(invoice_no) == _canonical(grn_header.challan_no.value)
            and _field_is_verified(invoice_header.doc_no)
            and _field_is_verified(grn_header.challan_no)
        )
    if direct_anchor == "challan_no":
        return bool(
            invoice_header.challan_no is not None
            and grn_header.challan_no is not None
            and _canonical(invoice_header.challan_no.value)
            == _canonical(grn_header.challan_no.value)
            and _field_is_verified(invoice_header.challan_no)
            and _field_is_verified(grn_header.challan_no)
        )
    grn_no = _doc_no(grn.document)
    # GRN-number references remain corroboration; the caller also requires an
    # independent part or amount signal before creating this edge.
    return bool(
        grn_no
        and invoice_header.grn_refs is not None
        and any(_canonical(value) == _canonical(grn_no) for value in invoice_header.grn_refs.value)
        and _field_is_verified(invoice_header.grn_refs)
    )


def _via_grn_resolution(
    source: MappingDocument,
    documents: tuple[MappingDocument, ...],
    purchase_orders: tuple[MappingPO, ...],
    phase_a: dict[UUID, LinkResolution],
    config: MappingConfig,
) -> tuple[tuple[ProposedLink, ...], ReasonCode | None, str | None]:
    matched_grns: list[tuple[MappingDocument, bool, str, str]] = []
    mismatched: list[str] = []
    for peer in documents:
        handwritten_grn = (
            peer.document.header.doc_type is DocType.GRN
            and source.document.header.doc_type is DocType.INVOICE
            and not _grn_reference_matches_invoice(source, peer)
            and _handwritten_grn_matches(source, peer)
        )
        if (
            peer.document.header.doc_type is DocType.GRN
            and source.document.header.doc_type is DocType.INVOICE
            and (_grn_reference_matches_invoice(source, peer) or handwritten_grn)
        ):
            if (
                source.vendor_id is None
                or peer.vendor_id is None
                or source.vendor_id != peer.vendor_id
            ):
                mismatched.append(_doc_no(peer.document) or "GRN supplier")
            else:
                independent_part = _strong_part_match(source.document, peer.document)
                independent_amount = _amount_in_band(source, peer.document, config)
                identifier_anchor = _invoice_grn_identifier_anchor(source, peer)
                if handwritten_grn:
                    # A handwritten GRN number is a hint: it needs an independent part or
                    # amount signal and every link it leads to stays a suggestion (WI-Q2).
                    identifier_anchor = None
                if identifier_anchor is not None or independent_part or independent_amount:
                    corroboration = (
                        "part" if independent_part else "amount" if independent_amount else "none"
                    )
                    matched_grns.append(
                        (
                            peer,
                            False
                            if handwritten_grn
                            else _verified_invoice_grn_anchor(source, peer),
                            corroboration,
                            "handwritten_grn_reference"
                            if handwritten_grn
                            else identifier_anchor or "invoice_grn_reference",
                        )
                    )
        elif (
            source.document.header.doc_type is DocType.GRN
            and peer.document.header.doc_type is DocType.INVOICE
            and _grn_reference_matches_invoice(peer, source)
        ):
            if (
                source.vendor_id is None
                or peer.vendor_id is None
                or source.vendor_id != peer.vendor_id
            ):
                mismatched.append(_doc_no(peer.document) or "invoice supplier")
            else:
                # GRN-to-invoice traversal cannot establish a PO edge; the GRN's
                # own verified PO relation is the only direction used below.
                continue

    invoice_grn_links: list[ProposedLink] = []
    invoice_po_links: list[ProposedLink] = []
    for grn, anchor_verified, corroboration, anchor_kind in sorted(
        matched_grns, key=lambda item: str(item[0].document_id)
    ):
        grn_number = _doc_no(grn.document) or "GRN"
        resolution = phase_a.get(grn.document_id)
        grn_links = (
            tuple(
                link
                for link in resolution.links
                if link.link_type is LinkType.GRN_PO
                and link.to_document_id not in source.rejected_po_ids
            )
            if resolution
            else ()
        )
        linked_ids = {link.to_document_id for link in grn_links}
        invoice_grn_state = (
            LinkState.AUTO
            if anchor_verified and config.confidence_via_grn >= config.link_auto_accept
            else LinkState.SUGGESTED
        )
        invoice_grn_state = (
            LinkState.SUGGESTED
            if invoice_grn_state is LinkState.AUTO
            and (split_unconfirmed(source.document) or split_unconfirmed(grn.document))
            else invoice_grn_state
        )
        relation_evidence = (
            f"Same-vendor invoice↔GRN reference {grn_number}; anchor={anchor_kind}; "
            f"independent_corroboration={corroboration}"
        )
        if split_unconfirmed(source.document) or split_unconfirmed(grn.document):
            relation_evidence += "; file split not confirmed"
        invoice_grn_links.append(
            ProposedLink(
                from_document_id=source.document_id,
                to_document_id=grn.document_id,
                link_type=LinkType.INV_GRN,
                level=LinkLevel.HEADER,
                method=LinkMethod.VIA_GRN,
                confidence=config.confidence_via_grn,
                state=invoice_grn_state,
                evidence=relation_evidence,
                via_document_id=grn.document_id,
            )
        )
        # The direct invoice↔GRN relation can stand on its own. A PO relation is
        # inherited only when this GRN already has an established GRN→PO edge.
        if not linked_ids:
            continue
        po_relation_state = (
            LinkState.AUTO
            if invoice_grn_state is LinkState.AUTO
            and all(link.state in {LinkState.AUTO, LinkState.CONFIRMED} for link in grn_links)
            else LinkState.SUGGESTED
        )
        grn_po_matches: list[tuple[MappingPO, LinkLevel, int | None, str]] = []
        for grn_link in grn_links:
            po_id = grn_link.to_document_id
            linked_po = next(
                (candidate for candidate in purchase_orders if candidate.document_id == po_id), None
            )
            if (
                source.vendor_id is not None
                and grn.vendor_id == source.vendor_id
                and linked_po is not None
                and linked_po.vendor_id == source.vendor_id
                and linked_po.document_id not in source.rejected_po_ids
            ):
                grn_po_matches.append(
                    (
                        linked_po,
                        LinkLevel.HEADER,
                        None,
                        f"Linked through GRN {grn_number}; anchor={anchor_kind}; "
                        f"independent_corroboration={corroboration}",
                    )
                )
        invoice_po_links.extend(
            _links_for_matches(
                source,
                tuple(grn_po_matches),
                method=LinkMethod.VIA_GRN,
                confidence=config.confidence_via_grn,
                config=config,
                via_document_id=grn.document_id,
                held=split_unconfirmed(grn.document),
                force_suggested=po_relation_state is LinkState.SUGGESTED,
            )
        )
    if invoice_grn_links or invoice_po_links:
        return (*invoice_grn_links, *invoice_po_links), None, None
    if mismatched:
        return (), ReasonCode.VENDOR_MISMATCH, mismatched[0]
    return (), None, None


def _manual_resolution(
    source: MappingDocument,
    purchase_orders: tuple[MappingPO, ...],
    config: MappingConfig,
) -> tuple[tuple[ProposedLink, ...], ReasonCode | None]:
    by_id = {po.document_id: po for po in purchase_orders}
    links: list[ProposedLink] = []
    mismatch = False
    for po_id in source.manual_po_ids:
        po = by_id.get(po_id)
        if po is None or po_id in source.rejected_po_ids:
            continue
        if source.vendor_id is None or po.vendor_id != source.vendor_id:
            mismatch = True
            continue
        links.extend(
            _links_for_matches(
                source,
                ((po, LinkLevel.HEADER, None, MANUAL_SELECTION_EVIDENCE),),
                method=LinkMethod.MANUAL,
                confidence=config.confidence_manual,
                config=config,
            )
        )
    return tuple(links), ReasonCode.VENDOR_MISMATCH if mismatch else None


def _resolution(
    source: MappingDocument,
    links: tuple[ProposedLink, ...] = (),
    *,
    candidates: tuple[LinkCandidate, ...] = (),
    reason_code: ReasonCode | None = None,
    reason_detail: str | None = None,
    reference_text: str | None = None,
) -> LinkResolution:
    if links:
        outcome = (
            ResolutionOutcome.SUGGESTED
            if candidates or any(link.state is LinkState.SUGGESTED for link in links)
            else ResolutionOutcome.LINKED
        )
        return LinkResolution(
            document_id=source.document_id,
            outcome=outcome,
            links=links,
            candidates=candidates,
            reason_code=reason_code,
            reason_detail=reason_detail,
            reference_text=reference_text,
        )
    return LinkResolution(
        document_id=source.document_id,
        outcome=ResolutionOutcome.UNLINKED,
        candidates=candidates,
        reason_code=reason_code or ReasonCode.NO_PO_LINK,
        reason_detail=reason_detail,
        reference_text=reference_text,
    )


def _header_date(document: Document) -> date | None:
    field = document.header.doc_date
    return field.value if field is not None else None


def _line_value(line: object, name: str) -> Decimal | None:
    field = getattr(line, name, None)
    return field.value if field is not None else None


def _line_quantity(line: object) -> Decimal | None:
    for field_name in ("quantity", "accepted_qty", "received_qty"):
        quantity = _line_value(line, field_name)
        if quantity is not None:
            return quantity
    return None


def _line_amount(line: object) -> Decimal:
    amount = _line_value(line, "amount")
    if amount is not None:
        return amount
    return (_line_quantity(line) or _ZERO) * (_line_value(line, "rate") or _ZERO)


def _extra_values(document: Document, names: set[str]) -> tuple[str, ...]:
    output: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).casefold() in names:
                    output.extend(_flatten(nested))
                else:
                    visit(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)

    visit(document.header.extras)
    return tuple(dict.fromkeys(item.strip() for item in output if item.strip()))


def _reference_score(source: MappingDocument, po: MappingPO) -> Decimal:
    po_no = _canonical(_doc_no(po.document))
    if not po_no:
        return _ZERO
    header_refs, _, _ = _header_refs(source.document)
    line_refs = tuple(reference for _, reference in _line_po_refs(source.document))
    if any(_canonical(value) == po_no for value in (*header_refs, *line_refs)):
        return _ONE

    marketplace_refs = _extra_values(
        source.document, {"marketplace_order_no", "marketplace_order_refs", "marketplace_order_ref"}
    )
    if marketplace_refs:
        po_text = " ".join(
            (
                po.document.raw_text or "",
                *_extra_values(
                    po.document,
                    {"narration", "notes", "remarks", "memo", "description", "order_reference"},
                ),
            )
        )
        if any(_contains_canonical_reference(po_text, reference) for reference in marketplace_refs):
            return _ONE
    return _ZERO


def _has_scoring_reference(source: MappingDocument) -> bool:
    header_refs, _, _ = _header_refs(source.document)
    line_refs = _line_po_refs(source.document)
    marketplace_refs = _extra_values(
        source.document, {"marketplace_order_no", "marketplace_order_refs", "marketplace_order_ref"}
    )
    return bool(header_refs or line_refs or marketplace_refs)


def _contains_canonical_reference(text: str, reference: str) -> bool:
    """Match a normalized reference as complete adjacent tokens, never a prefix."""

    reference_tokens = re.findall(r"[A-Za-z0-9]+", reference)
    text_tokens = tuple(re.finditer(r"[A-Za-z0-9]+", text))
    canonical_reference = _canonical(reference)
    if not canonical_reference or not reference_tokens:
        return False
    for start in range(len(text_tokens)):
        for end in range(start + 1, len(text_tokens) + 1):
            first = text_tokens[start]
            last = text_tokens[end - 1]
            canonical_window = _canonical(text[first.start() : last.end()])
            if canonical_window == canonical_reference:
                before = text[first.start() - 1] if first.start() else ""
                after = text[last.end()] if last.end() < len(text) else ""
                # Hyphen, slash, underscore and dot are common identifier
                # joiners; don't accept a leading or trailing identifier part.
                if (not before or before not in "-_/.") and (not after or after not in "-_/."):
                    return True
            if len(canonical_window) > len(canonical_reference):
                break
    return False


def _weighted_signal_score(
    reference: Decimal,
    part: Decimal,
    amount: Decimal | None,
    *,
    reference_available: bool,
    part_available: bool,
) -> Decimal:
    """Normalize the fixed signal weights over evidence available for this candidate."""

    weighted = _ZERO
    available_weight = _ZERO
    if reference_available:
        weighted += _REFERENCE_WEIGHT * reference
        available_weight += _REFERENCE_WEIGHT
    if part_available:
        weighted += _PART_WEIGHT * part
        available_weight += _PART_WEIGHT
    if amount is not None:
        weighted += _AMOUNT_WEIGHT * amount
        available_weight += _AMOUNT_WEIGHT
    if available_weight == _ZERO:
        return _ZERO
    return (weighted / available_weight).quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)


def _typed_refs(document: Document, ref_type: str) -> tuple[Mapping[str, Any], ...]:
    """Normalised typed handwritten references of one type (WI-Q2; hints, never fields)."""

    found: list[Mapping[str, Any]] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key) == "typed_refs" and isinstance(nested, list):
                    found.extend(
                        item
                        for item in nested
                        if isinstance(item, dict)
                        and item.get("type") == ref_type
                        and isinstance(item.get("normalised"), str)
                    )
                else:
                    visit(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)

    visit(document.header.extras)
    return tuple(found)


def _typed_ref_names(refs: tuple[Mapping[str, Any], ...], document_number: str | None) -> bool:
    """Whether a typed reference names this document: the whole number or its ``MM-NNN``.

    A short number is used only because the reference is typed (D88): a PO-type
    reference is compared with POs only and a GRN-type reference with GRNs only.
    """

    number = _canonical(document_number)
    short = short_of(document_number)
    return bool(
        number
        and any(
            _canonical(str(ref["normalised"])) == number
            or (short is not None and ref.get("short") == short)
            for ref in refs
        )
    )


def _annotation_ref_matches(source: MappingDocument, po: MappingPO) -> bool:
    po_no = _canonical(_doc_no(po.document))
    annotation_refs = _extra_values(
        source.document, {"annotation_refs", "handwritten_refs", "handwritten_references"}
    )
    if po_no and any(_canonical(value) == po_no for value in annotation_refs):
        return True
    return _typed_ref_names(_typed_refs(source.document, "po"), _doc_no(po.document))


def _handwritten_grn_matches(invoice: MappingDocument, grn: MappingDocument) -> bool:
    """A GRN-type handwritten reference on the invoice names this GRN (a hint only)."""

    if not isinstance(invoice.document.header, InvoiceHeader) or not isinstance(
        grn.document.header, GRNHeader
    ):
        return False
    return _typed_ref_names(_typed_refs(invoice.document, "grn"), _doc_no(grn.document))


def _gstin_value(document: Document, name: str) -> str | None:
    field = getattr(document.header, name, None)
    value = getattr(field, "value", None)
    return str(value).strip().upper() or None if value else None


def _grand_total(document: Document) -> Decimal | None:
    field = document.header.grand_total
    return field.value if field is not None and field.value is not None else None


def _within_rupee(left: Decimal | None, right: Decimal | None) -> bool:
    return (
        left is not None
        and right is not None
        and abs(left - right) <= _HANDWRITTEN_AMOUNT_TOLERANCE
    )


def _cites_po(grn: Document, po_number: str | None) -> bool:
    number = _canonical(po_number)
    refs = getattr(grn.header, "po_refs", None)
    values = refs.value if refs is not None and refs.value else ()
    return bool(number) and any(_canonical(str(value)) == number for value in values)


def _handwritten_header_links(
    source: MappingDocument,
    documents: tuple[MappingDocument, ...],
    purchase_orders: tuple[MappingPO, ...],
    existing: tuple[ProposedLink, ...],
    config: MappingConfig,
) -> tuple[ProposedLink, ...]:
    """Typed handwritten references corroborated at header level (WI-D4, owner 21 Oct).

    Suggestions only, never auto. A PO-type reference may point only to a PO and a
    GRN-type reference only to a GRN (D88). Every condition must hold: the vendor GSTIN
    on the invoice equals the target's; the invoice grand total equals the target's
    total within one rupee (for a PO, or the total of a GRN that cites that PO: a
    partial delivery); and no other document of that type has the same number and
    vendor. A handwritten GRN whose own PO reference is the handwritten PO is recorded
    as extra evidence on both links.
    """

    if not isinstance(source.document.header, InvoiceHeader):
        return ()
    vendor = _gstin_value(source.document, "vendor_gstin")
    total = _grand_total(source.document)
    if vendor is None or total is None:
        return ()
    grns = tuple(
        peer
        for peer in documents
        if peer.document.header.doc_type is DocType.GRN
        and _gstin_value(peer.document, "vendor_gstin") == vendor
    )
    linked = {(link.link_type, link.to_document_id) for link in existing}

    def unique(candidates: list[Any]) -> Any | None:
        return candidates[0] if len(candidates) == 1 else None

    po_match: MappingPO | None = None
    po_basis = ""
    for ref in _typed_refs(source.document, "po"):
        same_number = [
            po
            for po in purchase_orders
            if _gstin_value(po.document, "vendor_gstin") == vendor
            and _typed_ref_names((ref,), _doc_no(po.document))
        ]
        candidate = unique(same_number)
        if candidate is None:
            continue
        if _within_rupee(total, _grand_total(candidate.document)):
            po_match, po_basis = candidate, "invoice total = PO total"
        else:
            partial = [
                grn
                for grn in grns
                if _cites_po(grn.document, _doc_no(candidate.document))
                and _within_rupee(total, _grand_total(grn.document))
            ]
            if partial:
                po_match = candidate
                po_basis = f"invoice total = total of GRN {_doc_no(partial[0].document)}"
        if po_match is not None:
            break

    grn_match: MappingDocument | None = None
    for ref in _typed_refs(source.document, "grn"):
        candidate = unique([grn for grn in grns if _typed_ref_names((ref,), _doc_no(grn.document))])
        if candidate is not None and _within_rupee(total, _grand_total(candidate.document)):
            grn_match = candidate
            break

    cross = (
        po_match is not None
        and grn_match is not None
        and _cites_po(grn_match.document, _doc_no(po_match.document))
    )
    extra = "; handwritten GRN cites the handwritten PO" if cross else ""
    links: list[ProposedLink] = []
    if po_match is not None and (LinkType.INV_PO, po_match.document_id) not in linked:
        links.append(
            ProposedLink(
                from_document_id=source.document_id,
                to_document_id=po_match.document_id,
                link_type=LinkType.INV_PO,
                level=LinkLevel.HEADER,
                method=LinkMethod.LABELED,
                confidence=config.step3_min_score,
                state=LinkState.SUGGESTED,
                evidence=(
                    f"Handwritten PO reference {_doc_no(po_match.document)} (typed); "
                    f"vendor GSTIN matches; {po_basis} within 1; no other PO with that "
                    f"number and vendor{extra}"
                ),
            )
        )
    if grn_match is not None and (LinkType.INV_GRN, grn_match.document_id) not in linked:
        links.append(
            ProposedLink(
                from_document_id=source.document_id,
                to_document_id=grn_match.document_id,
                link_type=LinkType.INV_GRN,
                level=LinkLevel.HEADER,
                method=LinkMethod.VIA_GRN,
                confidence=config.step3_min_score,
                state=LinkState.SUGGESTED,
                evidence=(
                    f"Handwritten GRN reference {_doc_no(grn_match.document)} (typed); "
                    "vendor GSTIN matches; invoice total = GRN total within 1; no other "
                    f"GRN with that number and vendor{extra}"
                ),
                via_document_id=grn_match.document_id,
            )
        )
    return tuple(links)


def _part_identity(invoice_line: DocLine, po_line: DocLine, config: MappingConfig) -> Decimal:
    invoice_code = _line_value(invoice_line, "item_code")
    po_code = _line_value(po_line, "item_code")
    if invoice_code and po_code:
        left = re.sub(r"[^A-Z0-9]", "", str(invoice_code).upper())
        right = re.sub(r"[^A-Z0-9]", "", str(po_code).upper())
        if left == right:
            return _ONE
        # Conflicting printed part numbers cannot be rescued by similar prose.
        return _ZERO

    def model_tokens(line: object) -> set[str]:
        text = " ".join(
            str(value)
            for value in (
                _line_value(line, "description"),
                _line_value(line, "item_code"),
            )
            if value
        ).upper()
        return {
            token
            for token in re.findall(r"[A-Z0-9]+(?:[-/][A-Z0-9]+)*", text)
            if re.search(r"[A-Z]", token) and re.search(r"\d", token) and len(token) >= 3
        }

    left_tokens = model_tokens(invoice_line)
    right_tokens = model_tokens(po_line)
    if left_tokens and right_tokens:
        if left_tokens & right_tokens:
            return _ONE
        return _ZERO

    # HSN and UOM can help reconciliation but do not identify which PO line the
    # invoice line describes. Description is only the lower-credit fallback.
    description_only_alignment = config.alignment.model_copy(
        update={
            "weights": config.alignment.weights.model_copy(
                update={
                    "desc": _ONE,
                    "code": _ZERO,
                    "hsn": _ZERO,
                    "uom": _ZERO,
                }
            )
        }
    )
    aligned = align_lines((invoice_line,), (po_line,), description_only_alignment)
    if not aligned or aligned[0].score is None:
        return _ZERO
    # Exact identifiers earn full credit; description-only similarity is capped
    # at the existing lower-credit alignment boundary.
    return min(aligned[0].score, config.alignment.item_aligned)


def _amount_tolerance(
    source: MappingDocument,
    aligned_rows: tuple[tuple[int, int, Decimal], ...],
    expected: Decimal,
    config: MappingConfig,
) -> Decimal:
    """Apply the absolute floor only when the evidence covers the whole invoice."""

    all_invoice_lines = {line.line_no for line in source.document.lines}
    aligned_invoice_lines = {invoice_no for invoice_no, _, _ in aligned_rows}
    full_invoice_scope = bool(all_invoice_lines) and all_invoice_lines == aligned_invoice_lines
    absolute_floor = config.amount_tolerance_abs if full_invoice_scope else _ZERO
    return max(abs(expected) * config.amount_tolerance_pct / Decimal("100"), absolute_floor)


def _amount_score_and_variances(
    source: MappingDocument,
    po: MappingPO,
    aligned_rows: tuple[tuple[int, int, Decimal], ...],
    grn_qty: dict[int, Decimal],
    config: MappingConfig,
) -> tuple[Decimal | None, Decimal | None, Decimal, Decimal, Decimal]:
    invoice_lines = {line.line_no: line for line in source.document.lines}
    po_lines = {line.line_no: line for line in po.document.lines}
    expected = Decimal("0")
    actual = Decimal("0")
    quantity_variance = Decimal("0")
    price_variance = Decimal("0")
    complete_expected = bool(aligned_rows)
    complete_actual = bool(aligned_rows)
    for invoice_no, po_no, _ in aligned_rows:
        invoice_line = invoice_lines[invoice_no]
        po_line = po_lines[po_no]
        accepted_qty = grn_qty.get(po_no)
        po_rate = _line_value(po_line, "rate")
        invoice_qty = _line_quantity(invoice_line)
        invoice_rate = _line_value(invoice_line, "rate")
        if accepted_qty is None or po_rate is None:
            complete_expected = False
            continue
        expected += accepted_qty * po_rate
        if invoice_qty is not None and invoice_rate is not None:
            actual += invoice_qty * invoice_rate
            quantity_variance += (invoice_qty - accepted_qty) * po_rate
            price_variance += (invoice_rate - po_rate) * invoice_qty
        else:
            complete_actual = False
    if not complete_expected or not complete_actual:
        return None, None, expected, quantity_variance, price_variance
    delta = actual - expected
    absolute_delta = abs(delta)
    tolerance = _amount_tolerance(source, aligned_rows, expected, config)
    if absolute_delta <= tolerance:
        score = _ONE
    else:
        # Continuous, unbounded decay outside the full-credit band; amount
        # variance remains a score signal and never removes a candidate.
        score = _ONE / (_ONE + (absolute_delta - tolerance) / max(abs(expected), tolerance, _ONE))
    return (
        score.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP),
        delta,
        expected,
        quantity_variance,
        price_variance,
    )


def _grn_support(po: MappingPO, source: MappingDocument | None) -> dict[int, Decimal]:
    """Receipt quantity a check may use (WI-D4, D90): never the source's own receipt.

    With per-source support only other sources' auto or confirmed links and opening
    balances count; a source's own link, in any state, is never evidence in its own
    check. An older snapshot without per-source support keeps its ledger figures.
    """

    if po.grn_support_by_source is None:
        return po.grn_support_qty_by_line
    support = {line.line_no: _ZERO for line in po.document.lines}
    own = source.document_id if source is not None else None
    for source_id, lines in po.grn_support_by_source.items():
        if source_id == own:
            continue
        for line_no, quantity in lines.items():
            support[line_no] = support.get(line_no, _ZERO) + quantity
    return support


def _direct_grn_po_amount(source: MappingDocument, po: MappingPO, config: MappingConfig) -> bool:
    """A GRN's own lines against the PO's lines, without the ledger (WI-D4).

    Independent evidence for a GRN's PO reference: every goods line of the GRN aligns
    with a PO line and the GRN's quantities at the PO's rates equal the GRN's own
    amounts within the amount tolerance. It never reads the ledger, so the GRN's own
    receipt cannot support it.
    """

    if source.document.header.doc_type is not DocType.GRN:
        return False
    _, aligned, *_ = _content_breakdown(source, po, {}, config)
    goods = {
        line.line_no
        for line in source.document.lines
        if not (
            getattr(line, "line_type", None) is not None and line.line_type.value.value == "charge"  # type: ignore[union-attr]
        )
    }
    if not aligned or goods != {row[0] for row in aligned}:
        return False
    lines = {line.line_no: line for line in source.document.lines}
    received: dict[int, Decimal] = {}
    for grn_line_no, po_line_no, _ in aligned:
        quantity = _line_quantity(lines[grn_line_no])
        if quantity is None:
            return False
        received[po_line_no] = received.get(po_line_no, _ZERO) + quantity
    amount, *_ = _amount_score_and_variances(source, po, aligned, received, config)
    return amount == _ONE


def _content_breakdown(
    source: MappingDocument,
    po: MappingPO,
    grn_qty: dict[int, Decimal],
    config: MappingConfig,
) -> tuple[
    Decimal,
    tuple[tuple[int, int, Decimal], ...],
    dict[str, Decimal],
    tuple[str, ...],
    Decimal | None,
    Decimal,
    Decimal,
]:
    alignment: list[tuple[int, int, Decimal]] = []
    for invoice_line in source.document.lines:
        line_type = getattr(invoice_line, "line_type", None)
        if line_type is not None and line_type.value.value == "charge":
            continue
        scored_lines = [
            (_part_identity(invoice_line, po_line, config), po_line.line_no)
            for po_line in po.document.lines
        ]
        if not scored_lines:
            continue
        part_score, po_line_no = max(scored_lines, key=lambda item: (item[0], -item[1]))
        if part_score >= config.alignment.item_review:
            alignment.append((invoice_line.line_no, po_line_no, part_score))
    aligned_rows = tuple(sorted(alignment))
    invoice_lines = {line.line_no: line for line in source.document.lines}
    reference = _reference_score(source, po)
    if aligned_rows:
        line_weights = [
            max(_line_quantity(invoice_lines[row[0]]) or _ZERO, _ONE) for row in aligned_rows
        ]
        total_weight = sum(line_weights, _ZERO)
        part = (
            sum(
                (row[2] * weight for row, weight in zip(aligned_rows, line_weights, strict=True)),
                _ZERO,
            )
            / total_weight
        )
    else:
        part = _ZERO
    amount, amount_delta, expected, quantity_variance, price_variance = _amount_score_and_variances(
        source, po, aligned_rows, grn_qty, config
    )
    annotation_matches = _annotation_ref_matches(source, po)
    amount_tolerance = _amount_tolerance(source, aligned_rows, expected, config)
    annotation_amount_agrees = amount_delta is not None and abs(amount_delta) <= amount_tolerance
    if not reference and annotation_matches and annotation_amount_agrees:
        reference = _ONE
    score = _weighted_signal_score(
        reference,
        part,
        amount,
        reference_available=_has_scoring_reference(source) or bool(reference),
        part_available=bool(source.document.lines and po.document.lines),
    )
    reference_available = _has_scoring_reference(source) or bool(reference)
    part_available = bool(source.document.lines and po.document.lines)
    identity_score = _weighted_signal_score(
        reference,
        part,
        None,
        reference_available=reference_available,
        part_available=part_available,
    )
    signals = {
        "reference": reference,
        "part": part,
        "reference_part_score": identity_score,
    }
    missing: list[str] = []
    if not reference:
        missing.append("reference")
    if not part:
        missing.append("part/model or description alignment")
    if amount is None:
        missing.append("GRN accepted quantity × PO rate")
    if annotation_matches and not annotation_amount_agrees:
        missing.append("handwritten reference requires same-vendor amount corroboration")
    if amount is not None:
        signals["amount"] = amount
    if amount_delta is not None:
        signals["amount_tolerance"] = amount_tolerance
        signals["amount_delta"] = amount_delta
        signals["expected_grn_value"] = expected
        signals["quantity_variance_value"] = quantity_variance
        signals["price_variance_value"] = price_variance
    return (
        score,
        aligned_rows,
        signals,
        tuple(missing),
        amount_delta,
        quantity_variance,
        price_variance,
    )


def _line_candidate_breakdown(
    source: MappingDocument,
    po: MappingPO,
    invoice_line_no: int,
    po_line_no: int,
    part_score: Decimal,
    config: MappingConfig,
) -> tuple[Decimal, dict[str, Decimal], tuple[str, ...]]:
    """Score one invoice-line/PO-line identity; quantity and rate stay checks."""

    support = _grn_support(po, source)
    _, _, document_signals, document_missing, _, _, _ = _content_breakdown(
        source, po, support, config
    )
    row = (invoice_line_no, po_line_no, part_score)
    amount, amount_delta, expected, quantity_variance, price_variance = _amount_score_and_variances(
        source, po, (row,), support, config
    )
    invoice_line = next(
        (line for line in source.document.lines if line.line_no == invoice_line_no), None
    )
    po_line = next((line for line in po.document.lines if line.line_no == po_line_no), None)
    invoice_quantity = _line_quantity(invoice_line) if invoice_line is not None else None
    accepted_quantity = support.get(po_line_no)
    invoice_rate = _line_value(invoice_line, "rate") if invoice_line is not None else None
    po_rate = _line_value(po_line, "rate") if po_line is not None else None
    reference = document_signals.get("reference", _ZERO)
    annotation_is_scored = bool(reference) and not _has_scoring_reference(source)
    reference_available = _has_scoring_reference(source) or annotation_is_scored
    part_available = invoice_line is not None and po_line is not None
    identity_score = _weighted_signal_score(
        reference,
        part_score,
        None,
        reference_available=reference_available,
        part_available=part_available,
    )
    score = _weighted_signal_score(
        reference,
        part_score,
        amount,
        reference_available=reference_available,
        part_available=part_available,
    )
    signals = {
        "reference": reference,
        "part": part_score,
        "reference_part_score": identity_score,
    }
    missing = [item for item in document_missing if item != "part/model or description alignment"]
    if amount is None:
        missing.append("GRN accepted quantity × PO rate")
    else:
        signals["amount"] = amount
        signals["expected_grn_value"] = expected
        signals["amount_tolerance"] = _amount_tolerance(source, (row,), expected, config)
    if amount_delta is not None:
        signals["amount_delta"] = amount_delta
        signals["quantity_variance_value"] = quantity_variance
        signals["price_variance_value"] = price_variance
    for name, value in (
        ("invoice_quantity", invoice_quantity),
        ("accepted_grn_quantity", accepted_quantity),
        ("invoice_rate", invoice_rate),
        ("po_rate", po_rate),
    ):
        if value is not None:
            signals[name] = value
    return score, signals, tuple(dict.fromkeys(missing))


def _content_score(
    source: MappingDocument,
    po: MappingPO,
    open_qty: dict[int, Decimal],
    grn_qty: dict[int, Decimal],
    config: MappingConfig,
) -> tuple[Decimal, tuple[tuple[int, int, Decimal], ...]]:
    """Weighted identity score; quantity/rate/date remain explained checks."""

    del open_qty  # Capacity is applied by the period-wide assignment stage.
    score, aligned_rows, *_ = _content_breakdown(source, po, grn_qty, config)
    return score, aligned_rows


@dataclass(slots=True)
class _FlowEdge:
    to: int
    reverse: int
    capacity: Decimal
    initial_capacity: Decimal
    cost: int
    assignment_key: tuple[UUID, int, UUID, int] | None = None


def _add_flow_edge(
    graph: list[list[_FlowEdge]],
    start: int,
    end: int,
    capacity: Decimal,
    cost: int,
    *,
    assignment_key: tuple[UUID, int, UUID, int] | None = None,
) -> _FlowEdge:
    forward = _FlowEdge(end, len(graph[end]), capacity, capacity, cost, assignment_key)
    reverse = _FlowEdge(start, len(graph[start]), _ZERO, _ZERO, -cost)
    graph[start].append(forward)
    graph[end].append(reverse)
    return forward


def _max_weight_flow(graph: list[list[_FlowEdge]], source: int, sink: int) -> None:
    """Successive shortest augmenting paths with stable Bellman-Ford tie order."""

    while True:
        distance: list[int | None] = [None] * len(graph)
        parent: list[tuple[int, int] | None] = [None] * len(graph)
        distance[source] = 0
        for _ in range(len(graph) - 1):
            changed = False
            for node, edges in enumerate(graph):
                current = distance[node]
                if current is None:
                    continue
                for edge_index, edge in enumerate(edges):
                    if edge.capacity <= _ZERO:
                        continue
                    candidate = current + edge.cost
                    previous_distance = distance[edge.to]
                    if previous_distance is None or candidate < previous_distance:
                        distance[edge.to] = candidate
                        parent[edge.to] = (node, edge_index)
                        changed = True
            if not changed:
                break
        if parent[sink] is None:
            return
        flow: Decimal | None = None
        node = sink
        while node != source:
            previous = parent[node]
            if previous is None:
                return
            edge = graph[previous[0]][previous[1]]
            flow = edge.capacity if flow is None else min(flow, edge.capacity)
            node = previous[0]
        if flow is None or flow <= _ZERO:
            return
        node = sink
        while node != source:
            previous = parent[node]
            if previous is None:
                return
            edge = graph[previous[0]][previous[1]]
            edge.capacity -= flow
            graph[node][edge.reverse].capacity += flow
            node = previous[0]


def _candidate_evidence(
    source: MappingDocument,
    po: MappingPO,
    score: Decimal,
    signals: dict[str, Decimal],
    missing: tuple[str, ...],
    rejection_reasons: tuple[str, ...],
    config: MappingConfig,
    *,
    tie_break: str | None = None,
) -> str:
    parts = [
        f"identity={score:.4f}",
        "score_basis=available_signal_weights",
        "date=check_only",
        f"reference={signals.get('reference', _ZERO):.4f}/0.40",
        f"part={signals.get('part', _ZERO):.4f}/0.35",
        f"amount={signals.get('amount', _ZERO):.4f}/0.25",
    ]
    if "reference_part_score" in signals:
        parts.append(f"reference_part_identity={signals['reference_part_score']:.4f}")
    if "amount_delta" in signals:
        parts.extend(
            (
                f"amount_delta={signals['amount_delta']}",
                f"expected_grn_value={signals.get('expected_grn_value', _ZERO)}",
                f"quantity_variance_value={signals.get('quantity_variance_value', _ZERO)}",
                f"price_variance_value={signals.get('price_variance_value', _ZERO)}",
            )
        )
        amount_delta = signals["amount_delta"]
        amount_tolerance = signals.get("amount_tolerance")
        if amount_tolerance is None:
            expected = signals.get("expected_grn_value", _ZERO)
            amount_tolerance = max(
                abs(expected) * config.amount_tolerance_pct / Decimal("100"), _ZERO
            )
        if abs(amount_delta) <= amount_tolerance:
            parts.append("amount_check=pass:within_full_credit_band")
        elif amount_delta > _ZERO:
            parts.append("amount_check=fail:invoice_value_above_grn_value")
        else:
            parts.append("amount_check=warn:invoice_value_below_grn_value")
        decomposed = signals.get("quantity_variance_value", _ZERO) + signals.get(
            "price_variance_value", _ZERO
        )
        parts.append(
            "variance_decomposition=pass"
            if decomposed == amount_delta
            else "variance_decomposition=fail"
        )
    invoice_quantity = signals.get("invoice_quantity")
    accepted_quantity = signals.get("accepted_grn_quantity")
    if invoice_quantity is not None and accepted_quantity is not None:
        quantity_within = _tolerance_pass_config(
            invoice_quantity,
            accepted_quantity,
            config.quantity_tolerance_pct,
            config.quantity_tolerance_abs,
            config.limit_mode,
        )
        if quantity_within:
            parts.append("quantity_check=pass:within_configured_tolerance")
        elif invoice_quantity > accepted_quantity:
            parts.append("quantity_check=fail:invoice_quantity_above_grn_accepted")
        else:
            parts.append("quantity_check=warn:invoice_quantity_below_grn_accepted")
    invoice_rate = signals.get("invoice_rate")
    po_rate = signals.get("po_rate")
    if invoice_rate is not None and po_rate is not None:
        rate_within = _tolerance_pass_config(
            invoice_rate,
            po_rate,
            config.rate_tolerance_pct,
            config.rate_tolerance_abs,
            config.limit_mode,
        )
        if rate_within:
            parts.append("price_check=pass:within_configured_tolerance")
        elif invoice_rate > po_rate:
            parts.append("price_check=fail:invoice_rate_above_po_rate")
        else:
            parts.append("price_check=warn:invoice_rate_below_po_rate")
    parts.extend(_date_check_evidence(source, po, config))
    if missing:
        parts.append("missing=" + ", ".join(missing))
    if rejection_reasons:
        parts.append("review=" + ", ".join(rejection_reasons))
    if tie_break:
        parts.append("tie_break=" + tie_break)
    return "; ".join(parts)


def _date_check_evidence(
    source: MappingDocument,
    po: MappingPO,
    config: MappingConfig,
) -> tuple[str, ...]:
    invoice_date = _header_date(source.document)
    po_date = _header_date(po.document)
    before_po = invoice_date is not None and po_date is not None and invoice_date < po_date
    parts = ["date_check=invoice_before_po" if before_po else "date_check=not_flagged"]
    if invoice_date is not None and po_date is not None:
        days = abs((invoice_date - po_date).days)
        bucket = (
            "normal"
            if days <= config.date_normal_days
            else (
                "review_lower_confidence"
                if days <= config.date_review_days
                else "strong_mismatch_manual_verification"
            )
        )
        parts.append(f"date_proximity={days}_days:{bucket}")
        proximity_outcome = (
            "pass:normal"
            if days <= config.date_normal_days
            else (
                "warn:review_lower_confidence"
                if days <= config.date_review_days
                else "fail:strong_mismatch_manual_verification"
            )
        )
        parts.append(f"date_proximity_check={proximity_outcome}")
    else:
        parts.append("date_proximity_check=not_evaluated:missing_invoice_or_po_date")
    if before_po:
        parts.append("review=INVOICE_BEFORE_PO")
    return tuple(parts)


def _tolerance_pass_config(
    actual: Decimal,
    expected: Decimal,
    pct: Decimal,
    absolute: Decimal,
    mode: ToleranceMode,
) -> bool:
    variance = abs(actual - expected)
    within_abs = variance <= absolute
    pct_variance = (
        variance / abs(expected) * Decimal("100")
        if expected != _ZERO
        else (_ZERO if variance == _ZERO else Decimal("100"))
    )
    within_pct = pct_variance <= pct if expected != _ZERO else variance == _ZERO
    return within_abs or within_pct if mode is ToleranceMode.OR else within_abs and within_pct


def _line_date_tie_key(
    source: MappingDocument,
    po: MappingPO,
    invoice_line_no: int,
    po_line_no: int,
    config: MappingConfig,
    grn_dates_by_po: dict[UUID, tuple[date, ...]],
) -> tuple[int, int, Decimal]:
    invoice_date = _header_date(source.document)
    dates = grn_dates_by_po.get(po.document_id, ())
    if not dates and isinstance(po.document.header, POHeader):
        delivery_date = po.document.header.delivery_date
        dates = (delivery_date.value,) if delivery_date is not None else ()
    if not dates:
        po_date = _header_date(po.document)
        dates = (po_date,) if po_date is not None else ()
    days = (
        min(abs((invoice_date - item).days) for item in dates)
        if invoice_date is not None and dates
        else 10**9
    )
    bucket = 0 if days <= config.date_normal_days else 1 if days <= config.date_review_days else 2
    invoice_line = next(
        (line for line in source.document.lines if line.line_no == invoice_line_no), None
    )
    quantity = _line_quantity(invoice_line) if invoice_line is not None else None
    open_grn = po.open_grn_qty_by_line.get(po_line_no)
    if quantity is None or open_grn is None:
        capacity_distance = Decimal("1E100")
    else:
        capacity_distance = abs(quantity - open_grn)
    return bucket, days, capacity_distance


def _effective_line_capacities(po: MappingPO) -> dict[int, Decimal]:
    rates = {line.line_no: _line_value(line, "rate") or _ZERO for line in po.document.lines}
    quantities = {line_no: max(_ZERO, value) for line_no, value in po.open_qty_by_line.items()}
    implied_value = sum(
        (qty * rates.get(line_no, _ZERO) for line_no, qty in quantities.items()),
        _ZERO,
    )
    balance = max(_ZERO, po.open_balance)
    value_ratio = (
        min(_ONE, balance / implied_value)
        if implied_value > _ZERO
        else (_ONE if balance > _ZERO else _ZERO)
    )
    return {line_no: qty * value_ratio for line_no, qty in quantities.items()}


def _period_invoice_resolutions(
    sources: tuple[MappingDocument, ...],
    purchase_orders: tuple[MappingPO, ...],
    config: MappingConfig,
    *,
    grn_dates_by_po: dict[UUID, tuple[date, ...]],
) -> dict[UUID, LinkResolution]:
    """Resolve invoice lines jointly by maximum score under PO quantity/value capacity."""

    details: dict[UUID, dict[UUID, ContentDetail]] = {}
    line_candidates: dict[tuple[UUID, int], list[LineCandidate]] = {}
    ambiguous: set[tuple[UUID, int]] = set()
    allowed: dict[tuple[UUID, int], set[tuple[UUID, int]]] = {}
    tie_break_applied: set[tuple[UUID, int]] = set()
    rendered_candidates: dict[UUID, tuple[LinkCandidate, ...]] = {}

    for source in sources:
        if source.document.header.doc_type is not DocType.INVOICE or source.vendor_id is None:
            continue
        source_details: dict[
            UUID,
            ContentDetail,
        ] = {}
        for po in purchase_orders:
            if po.vendor_id != source.vendor_id or po.document_id in source.rejected_po_ids:
                continue
            score, aligned, signals, missing, _, _, _ = _content_breakdown(
                source, po, _grn_support(po, source), config
            )
            # Amount is never a retrieval gate: either a reference or an item
            # identity signal must retrieve the candidate.
            if not signals.get("reference") and not signals.get("part"):
                continue
            rejection_reasons: list[str] = []
            invoice_date = _header_date(source.document)
            po_date = _header_date(po.document)
            if invoice_date is not None and po_date is not None and invoice_date < po_date:
                rejection_reasons.append("INVOICE_BEFORE_PO")
            if isinstance(po.document.header, POHeader) and po.document.header.status is not None:
                if po.document.header.status.value is POStatus.CANCELLED:
                    rejection_reasons.append("PO_CANCELLED")
                elif po.document.header.status.value is POStatus.CLOSED:
                    rejection_reasons.append("PO_CLOSED")
            source_details[po.document_id] = (
                score,
                aligned,
                signals,
                missing,
                tuple(rejection_reasons),
            )
            for invoice_no, po_no, _ in aligned:
                line_score, line_signals, line_missing = _line_candidate_breakdown(
                    source, po, invoice_no, po_no, _, config
                )
                line_candidates.setdefault((source.document_id, invoice_no), []).append(
                    (
                        po,
                        line_score,
                        po_no,
                        Decimal("0"),
                        line_signals,
                        line_missing,
                        tuple(rejection_reasons),
                    )
                )
        details[source.document_id] = source_details
        rendered_candidates[source.document_id] = tuple(
            LinkCandidate(
                po_document_id=po.document_id,
                po_no=_doc_no(po.document) or "",
                score=score,
                evidence=_candidate_evidence(
                    source,
                    po,
                    score,
                    signals,
                    missing,
                    rejection_reasons,
                    config,
                ),
                signals=signals,
                missing_signals=missing,
                rejection_reasons=rejection_reasons,
            )
            for po in sorted(purchase_orders, key=lambda item: str(item.document_id))
            if (detail := source_details.get(po.document_id)) is not None
            for score, _aligned, signals, missing, rejection_reasons in (detail,)
        )

    # Within the configured lead margin, date proximity and open GRN quantity
    # break a tie; if those checks are also tied, both remain for review.
    for key, candidates in line_candidates.items():
        candidates.sort(key=lambda item: (-item[1], str(item[0].document_id), item[2]))
        viable = [
            item
            for item in candidates
            if item[4].get("reference_part_score", item[1]) >= config.step3_min_score
        ]
        if not viable:
            if len(candidates) > 1 and candidates[0][1] - candidates[1][1] < config.step3_min_lead:
                ambiguous.add(key)
            allowed[key] = set()
            continue
        if len(viable) > 1 and viable[0][1] - viable[1][1] < config.step3_min_lead:
            source = next(item for item in sources if item.document_id == key[0])
            tied = [item for item in viable if viable[0][1] - item[1] < config.step3_min_lead]
            ordered_ties = sorted(
                tied,
                key=lambda item: _line_date_tie_key(
                    source, item[0], key[1], item[2], config, grn_dates_by_po
                ),
            )
            best_key = _line_date_tie_key(
                source, ordered_ties[0][0], key[1], ordered_ties[0][2], config, grn_dates_by_po
            )
            same_best = [
                item
                for item in ordered_ties
                if _line_date_tie_key(source, item[0], key[1], item[2], config, grn_dates_by_po)
                == best_key
            ]
            if len(same_best) != 1:
                ambiguous.add(key)
                allowed[key] = set()
            else:
                allowed[key] = {(same_best[0][0].document_id, same_best[0][2])}
                tie_break_applied.add(key)
        else:
            # Outside the documented lead margin, only the highest-scoring PO
            # may be assigned; capacity must not silently redirect identity.
            allowed[key] = {(viable[0][0].document_id, viable[0][2])}

    all_invoice_lines = [
        (source, line)
        for source in sources
        if source.document.header.doc_type is DocType.INVOICE
        for line in source.document.lines
        if (quantity := _line_quantity(line)) is not None and quantity > _ZERO
    ]
    po_line_keys = [
        (po.document_id, line.line_no)
        for po in sorted(purchase_orders, key=lambda item: str(item.document_id))
        for line in sorted(po.document.lines, key=lambda item: item.line_no)
    ]
    source_node = 0
    invoice_node = {
        (source.document_id, line.line_no): index + 1
        for index, (source, line) in enumerate(all_invoice_lines)
    }
    po_node = {item: 1 + len(all_invoice_lines) + index for index, item in enumerate(po_line_keys)}
    sink_node = 1 + len(all_invoice_lines) + len(po_line_keys)
    graph: list[list[_FlowEdge]] = [[] for _ in range(sink_node + 1)]
    assignment_edges: list[_FlowEdge] = []
    po_by_id = {po.document_id: po for po in purchase_orders}
    for source, line in all_invoice_lines:
        key = (source.document_id, line.line_no)
        node = invoice_node[key]
        quantity = _line_quantity(line) or _ZERO
        _add_flow_edge(graph, source_node, node, quantity, 0)
        for po, score, po_line_no, _, _signals, _missing, _reasons in sorted(
            line_candidates.get(key, ()), key=lambda item: (-item[1], str(item[0].document_id))
        ):
            if (po.document_id, po_line_no) not in allowed.get(key, set()):
                continue
            target_node = po_node[(po.document_id, po_line_no)]
            flow_edge = _add_flow_edge(
                graph,
                node,
                target_node,
                quantity,
                -int(score * Decimal("1000000")),
                assignment_key=(source.document_id, line.line_no, po.document_id, po_line_no),
            )
            assignment_edges.append(flow_edge)
    for po in purchase_orders:
        capacities = _effective_line_capacities(po)
        for po_line_key, node in po_node.items():
            po_id, po_line_no = po_line_key
            if po_id == po.document_id:
                _add_flow_edge(graph, node, sink_node, capacities.get(po_line_no, _ZERO), 0)
    _max_weight_flow(graph, source_node, sink_node)
    assigned: dict[
        tuple[UUID, int],
        list[AssignedCandidate],
    ] = {}
    for edge in assignment_edges:
        if edge.assignment_key is None:
            continue
        flow = edge.initial_capacity - edge.capacity
        if flow <= _ZERO:
            continue
        source_id, line_no, po_id, po_line_no = edge.assignment_key
        po = po_by_id[po_id]
        candidate = next(
            item
            for item in line_candidates[(source_id, line_no)]
            if item[0].document_id == po_id and item[2] == po_line_no
        )
        assigned.setdefault((source_id, line_no), []).append(
            (po, po_line_no, flow, candidate[1], candidate[4], candidate[5], candidate[6])
        )

    results: dict[UUID, LinkResolution] = {}
    used_po_qty: dict[tuple[UUID, int], Decimal] = {}
    for source in sources:
        if source.document.header.doc_type is not DocType.INVOICE:
            continue
        proposals: list[ProposedLink] = []
        reason_detail: str | None = None
        for line in sorted(source.document.lines, key=lambda item: item.line_no):
            key = (source.document_id, line.line_no)
            candidates = line_candidates.get(key, [])
            if key in ambiguous:
                reason_detail = "CONTENT_AMBIGUOUS"
                continue
            selected = assigned.get(key, [])
            if not selected:
                fallback = next(
                    (
                        item
                        for item in sorted(
                            candidates,
                            key=lambda item: (-item[1], str(item[0].document_id)),
                        )
                        if item[4].get("reference_part_score", item[1]) >= config.step3_min_score
                        and (item[0].document_id, item[2]) in allowed.get(key, set())
                    ),
                    None,
                )
                if fallback is None:
                    if candidates:
                        reason_detail = reason_detail or "CONTENT_BELOW_THRESHOLD"
                    continue
                selected = [
                    (
                        fallback[0],
                        fallback[2],
                        _ZERO,
                        fallback[1],
                        fallback[4],
                        fallback[5],
                        fallback[6],
                    )
                ]
            for po, po_line_no, allocated, score, signals, missing, candidate_reasons in selected:
                rejection_reasons = list(candidate_reasons)
                requested = _line_quantity(line) or _ZERO
                capacity_key = (po.document_id, po_line_no)
                open_qty = max(
                    _ZERO,
                    po.open_qty_by_line.get(po_line_no, _ZERO)
                    - used_po_qty.get(capacity_key, _ZERO),
                )
                if requested > open_qty:
                    rejection_reasons.append("OPEN_PO_QUANTITY_EXCEEDED")
                tie_break = None
                if key in tie_break_applied:
                    tie_break = "date_proximity+open_grn_quantity"
                    open_grn_total = po.open_grn_qty_by_line.get(po_line_no)
                    if open_grn_total is not None:
                        tie_break += f"(open_grn_quantity={open_grn_total})"
                evidence = _candidate_evidence(
                    source,
                    po,
                    score,
                    signals,
                    missing,
                    tuple(rejection_reasons),
                    config,
                    tie_break=tie_break,
                )
                if split_unconfirmed(source.document) or split_unconfirmed(po.document):
                    evidence += "; file split not confirmed"
                evidence += (
                    f"; assigned_quantity={allocated}; invoice_quantity={requested}; "
                    f"open_po_quantity={open_qty}; "
                    f"assignment=period_max_weight"
                )
                used_po_qty[capacity_key] = used_po_qty.get(capacity_key, _ZERO) + requested
                proposals.append(
                    ProposedLink(
                        from_document_id=source.document_id,
                        to_document_id=po.document_id,
                        link_type=LinkType.INV_PO,
                        level=LinkLevel.LINE,
                        method=LinkMethod.CONTENT,
                        confidence=min(score, Decimal("0.90")),
                        state=LinkState.SUGGESTED,
                        evidence=evidence,
                        line_no=line.line_no,
                    )
                )
        candidates_for_source = rendered_candidates.get(source.document_id, ())
        if proposals:
            results[source.document_id] = _resolution(
                source,
                tuple(proposals),
                candidates=candidates_for_source,
                reason_detail=reason_detail,
            )
        else:
            results[source.document_id] = _resolution(
                source,
                candidates=candidates_for_source,
                reason_code=ReasonCode.NO_PO_LINK,
                reason_detail=reason_detail
                or ("CONTENT_AMBIGUOUS" if candidates_for_source else None),
            )
    return results


def _strong_part_match(left: Document, right: Document) -> bool:
    def values(line: object) -> set[str]:
        raw_code = _line_value(line, "item_code")
        raw_description = _line_value(line, "description")
        text = " ".join(str(value) for value in (raw_code, raw_description) if value).upper()
        return {
            token
            for token in re.findall(r"[A-Z0-9]+(?:[-/][A-Z0-9]+)*", text)
            if re.search(r"[A-Z]", token) and re.search(r"\d", token) and len(token) >= 3
        }

    for left_line in left.lines:
        for right_line in right.lines:
            left_code = _line_value(left_line, "item_code")
            right_code = _line_value(right_line, "item_code")
            if left_code and right_code:
                normalized_left = re.sub(r"[^A-Z0-9]", "", str(left_code).upper())
                normalized_right = re.sub(r"[^A-Z0-9]", "", str(right_code).upper())
                if normalized_left != normalized_right:
                    continue
                return True
            if values(left_line) & values(right_line):
                return True
    return False


def _amount_in_band(
    source: MappingDocument,
    target: Document,
    config: MappingConfig,
) -> bool:
    left = source.document.header.taxable_total or source.document.header.grand_total
    right = target.header.taxable_total or target.header.grand_total
    if left is None or right is None:
        return False
    delta = abs(left.value - right.value)
    tolerance = max(
        abs(right.value) * config.amount_tolerance_pct / Decimal("100"),
        config.amount_tolerance_abs,
    )
    return delta <= tolerance


def _field_has_system_confidence(field: object, config: MappingConfig) -> bool:
    origin = getattr(field, "origin", None)
    confidence = getattr(field, "confidence", None)
    return (
        origin in {FieldOrigin.OCR, FieldOrigin.ERP_IMPORT}
        and confidence is not None
        and confidence >= config.reference_system_check_min_confidence
    )


def _system_check_reference_fields(
    documents: tuple[MappingDocument, ...],
    purchase_orders: tuple[MappingPO, ...],
    config: MappingConfig,
) -> tuple[tuple[MappingDocument, ...], tuple[FieldVerificationUpdate, ...]]:
    """Verify exact OCR/ERP refs only with same-vendor and independent evidence."""

    updated_documents: list[MappingDocument] = []
    proposals: list[FieldVerificationUpdate] = []
    for source in documents:
        header = source.document.header
        header_updates: dict[str, object] = {}
        for field_name in ("po_refs",):
            field = getattr(header, field_name, None)
            if field is None or _field_is_user_verified(field):
                continue
            was_system_checked = field.verification is FieldVerification.SYSTEM_CHECK
            values = tuple(str(value) for value in field.value)
            if was_system_checked:
                still_identifies_po = (
                    len(values) == 1
                    and source.vendor_id is not None
                    and any(
                        po.vendor_id == source.vendor_id
                        and _canonical(_doc_no(po.document)) == _canonical(values[0])
                        for po in purchase_orders
                    )
                )
                if still_identifies_po:
                    # Verification records that the reference passed its system
                    # check. Later billing must not erase that identity evidence;
                    # capacity is reported separately on the proposed link.
                    header_updates[field_name] = field
                    continue
                header_updates[field_name] = field.model_copy(
                    update={"verification": FieldVerification.NONE}
                )
            if len(values) != 1 or not _field_has_system_confidence(field, config):
                if was_system_checked:
                    proposals.append(
                        FieldVerificationUpdate(
                            document_id=source.document_id,
                            field_name=field_name,
                            value_text=str(field.value),
                            verification=FieldVerification.NONE,
                        )
                    )
                continue
            reference = values[0]
            match_id: UUID | None = None
            if field_name == "po_refs":
                for po in purchase_orders:
                    if (
                        source.vendor_id is None
                        or source.vendor_id != po.vendor_id
                        or _canonical(_doc_no(po.document)) != _canonical(reference)
                    ):
                        continue
                    if not _po_is_open(po):
                        continue
                    independent_part = _strong_part_match(source.document, po.document)
                    _, _, po_signals, _, _, _, _ = _content_breakdown(
                        source, po, _grn_support(po, source), config
                    )
                    independent_amount = po_signals.get("amount") == _ONE or _direct_grn_po_amount(
                        source, po, config
                    )
                    if independent_part or independent_amount:
                        match_id = po.document_id
                        break
            if match_id is None:
                if was_system_checked:
                    proposals.append(
                        FieldVerificationUpdate(
                            document_id=source.document_id,
                            field_name=field_name,
                            value_text=str(field.value),
                            verification=FieldVerification.NONE,
                        )
                    )
                continue
            header_updates[field_name] = field.model_copy(
                update={"verification": FieldVerification.SYSTEM_CHECK}
            )
            proposals.append(
                FieldVerificationUpdate(
                    document_id=source.document_id,
                    field_name=field_name,
                    matched_document_id=match_id,
                    value_text=str(field.value),
                )
            )

        line_updates: dict[int, DocLine] = {}
        for line in source.document.lines:
            field = line.po_ref
            if field is None or _field_is_user_verified(field):
                continue
            was_system_checked = field.verification is FieldVerification.SYSTEM_CHECK
            if was_system_checked:
                still_identifies_po = source.vendor_id is not None and any(
                    po.vendor_id == source.vendor_id
                    and _canonical(_doc_no(po.document)) == _canonical(field.value)
                    for po in purchase_orders
                )
                if still_identifies_po:
                    line_updates[line.line_no] = line.model_copy(update={"po_ref": field})
                    continue
                line_updates[line.line_no] = line.model_copy(
                    update={
                        "po_ref": field.model_copy(update={"verification": FieldVerification.NONE})
                    }
                )
            if not _field_has_system_confidence(field, config):
                if was_system_checked:
                    proposals.append(
                        FieldVerificationUpdate(
                            document_id=source.document_id,
                            field_name="po_ref",
                            value_text=str(field.value),
                            line_no=line.line_no,
                            verification=FieldVerification.NONE,
                        )
                    )
                continue
            matches = tuple(
                po
                for po in purchase_orders
                if source.vendor_id is not None
                and po.vendor_id == source.vendor_id
                and _canonical(_doc_no(po.document)) == _canonical(field.value)
                and _po_is_open(po)
                and (
                    _strong_part_match(
                        source.document.model_copy(update={"lines": (line,)}), po.document
                    )
                    or _content_breakdown(source, po, _grn_support(po, source), config)[2].get(
                        "amount"
                    )
                    == _ONE
                    or _direct_grn_po_amount(source, po, config)
                )
            )
            if len(matches) != 1:
                if was_system_checked:
                    proposals.append(
                        FieldVerificationUpdate(
                            document_id=source.document_id,
                            field_name="po_ref",
                            value_text=str(field.value),
                            line_no=line.line_no,
                            verification=FieldVerification.NONE,
                        )
                    )
                continue
            line_updates[line.line_no] = line.model_copy(
                update={
                    "po_ref": field.model_copy(
                        update={"verification": FieldVerification.SYSTEM_CHECK}
                    )
                }
            )
            proposals.append(
                FieldVerificationUpdate(
                    document_id=source.document_id,
                    field_name="po_ref",
                    matched_document_id=matches[0].document_id,
                    value_text=str(field.value),
                    line_no=line.line_no,
                )
            )
        if header_updates or line_updates:
            lines = tuple(line_updates.get(line.line_no, line) for line in source.document.lines)
            updated_document = source.document.model_copy(
                update={
                    "header": header.model_copy(update=header_updates),
                    "lines": lines,
                }
            )
            source = source.model_copy(update={"document": updated_document})
        updated_documents.append(source)
    return tuple(updated_documents), tuple(
        sorted(
            proposals,
            key=lambda item: (str(item.document_id), item.field_name, item.line_no or 0),
        )
    )


def _system_check_invoice_grn_references(
    documents: tuple[MappingDocument, ...],
    purchase_orders: tuple[MappingPO, ...],
    phase_a: dict[UUID, LinkResolution],
    config: MappingConfig,
) -> tuple[tuple[MappingDocument, ...], tuple[FieldVerificationUpdate, ...]]:
    """Verify OCR GRN refs only through an existing same-vendor GRN→PO link."""

    po_by_id = {po.document_id: po for po in purchase_orders}
    updated_documents: list[MappingDocument] = []
    proposals: list[FieldVerificationUpdate] = []
    for source in documents:
        header = source.document.header
        field = header.grn_refs if isinstance(header, InvoiceHeader) else None
        header_updates: dict[str, object] = {}
        if field is not None and not _field_is_user_verified(field):
            was_system_checked = field.verification is FieldVerification.SYSTEM_CHECK
            values = tuple(str(value) for value in field.value)
            if was_system_checked:
                reference = values[0] if len(values) == 1 else None
                still_identifies_grn = False
                if reference is not None and source.vendor_id is not None:
                    for grn in documents:
                        if (
                            grn.document.header.doc_type is not DocType.GRN
                            or grn.vendor_id != source.vendor_id
                            or _canonical(_doc_no(grn.document)) != _canonical(reference)
                            or not _grn_reference_matches_invoice(source, grn)
                        ):
                            continue
                        grn_resolution = phase_a.get(grn.document_id)
                        if grn_resolution is not None and any(
                            link.link_type is LinkType.GRN_PO
                            and link.state in {LinkState.AUTO, LinkState.CONFIRMED}
                            and (po := po_by_id.get(link.to_document_id)) is not None
                            and po.vendor_id == source.vendor_id
                            for link in grn_resolution.links
                        ):
                            still_identifies_grn = True
                            break
                if still_identifies_grn:
                    # Keep an established corroborating reference after its GRN
                    # has been fully consumed; open quantity is a separate check.
                    header_updates["grn_refs"] = field
                    source = source.model_copy(
                        update={
                            "document": source.document.model_copy(
                                update={"header": header.model_copy(update=header_updates)}
                            )
                        }
                    )
                    updated_documents.append(source)
                    continue
                header_updates["grn_refs"] = field.model_copy(
                    update={"verification": FieldVerification.NONE}
                )
            matches: set[UUID] = set()
            if len(values) == 1 and _field_has_system_confidence(field, config):
                reference = values[0]
                for grn in documents:
                    if (
                        grn.document.header.doc_type is not DocType.GRN
                        or source.vendor_id is None
                        or source.vendor_id != grn.vendor_id
                        or _canonical(_doc_no(grn.document)) != _canonical(reference)
                        or not _grn_reference_matches_invoice(source, grn)
                    ):
                        continue
                    if not (
                        _strong_part_match(source.document, grn.document)
                        or _amount_in_band(source, grn.document, config)
                    ):
                        continue
                    grn_resolution = phase_a.get(grn.document_id)
                    if grn_resolution is None:
                        continue
                    linked_po_ids = {
                        link.to_document_id
                        for link in grn_resolution.links
                        if link.link_type is LinkType.GRN_PO
                        and link.state in {LinkState.AUTO, LinkState.CONFIRMED}
                    }
                    for po_id in linked_po_ids:
                        po = po_by_id.get(po_id)
                        if po is None or po.vendor_id != source.vendor_id:
                            continue
                        matches.add(grn.document_id)
            match_id = next(iter(matches)) if len(matches) == 1 else None
            if match_id is not None:
                header_updates["grn_refs"] = field.model_copy(
                    update={"verification": FieldVerification.SYSTEM_CHECK}
                )
                proposals.append(
                    FieldVerificationUpdate(
                        document_id=source.document_id,
                        field_name="grn_refs",
                        matched_document_id=match_id,
                        value_text=str(field.value),
                    )
                )
            elif was_system_checked:
                proposals.append(
                    FieldVerificationUpdate(
                        document_id=source.document_id,
                        field_name="grn_refs",
                        value_text=str(field.value),
                        verification=FieldVerification.NONE,
                    )
                )
        if header_updates:
            source = source.model_copy(
                update={
                    "document": source.document.model_copy(
                        update={"header": header.model_copy(update=header_updates)}
                    )
                }
            )
        updated_documents.append(source)
    return tuple(updated_documents), tuple(
        sorted(proposals, key=lambda item: (str(item.document_id), item.field_name))
    )


def _reserve_invoice_lines(
    source: MappingDocument,
    po_id: UUID,
    po_by_id: dict[UUID, MappingPO],
    remaining_qty: dict[UUID, dict[int, Decimal]],
    remaining_balance: dict[UUID, Decimal],
    config: MappingConfig,
    line_numbers: set[int] | None = None,
    *,
    remaining_open_grn_qty: dict[UUID, dict[int, Decimal]] | None = None,
) -> None:
    po = po_by_id.get(po_id)
    if po is None or source.document.header.doc_type is not DocType.INVOICE:
        return
    alignment = align_lines(
        source.document.lines,
        po.document.lines,
        config.alignment,
        explicit_link=True,
    )
    invoice_lines = {line.line_no: line for line in source.document.lines}
    amount_reserved = _ZERO
    for match in alignment:
        if match.po_line_no is None:
            continue
        invoice_line = invoice_lines.get(match.invoice_line_no)
        if invoice_line is None or (
            line_numbers is not None and invoice_line.line_no not in line_numbers
        ):
            continue
        quantity = _line_quantity(invoice_line) or _ZERO
        remaining_qty[po_id][match.po_line_no] = max(
            _ZERO, remaining_qty[po_id].get(match.po_line_no, _ZERO) - quantity
        )
        if remaining_open_grn_qty is not None:
            remaining_open_grn_qty[po_id][match.po_line_no] = max(
                _ZERO, remaining_open_grn_qty[po_id].get(match.po_line_no, _ZERO) - quantity
            )
        amount_reserved += _line_amount(invoice_line)
    remaining_balance[po_id] = max(_ZERO, remaining_balance.get(po_id, _ZERO) - amount_reserved)


def _content_invoice_resolution(
    source: MappingDocument,
    purchase_orders: tuple[MappingPO, ...],
    remaining_qty: dict[UUID, dict[int, Decimal]],
    remaining_grn_qty: dict[UUID, dict[int, Decimal]],
    remaining_balance: dict[UUID, Decimal],
    config: MappingConfig,
    prior_links: tuple[ProposedLink, ...],
) -> LinkResolution:
    del remaining_qty, remaining_grn_qty, remaining_balance
    result = _period_invoice_resolutions((source,), purchase_orders, config, grn_dates_by_po={})[
        source.document_id
    ]
    if not prior_links:
        return result
    return _resolution(
        source,
        (*prior_links, *result.links),
        candidates=result.candidates,
        reason_code=result.reason_code,
        reason_detail=result.reason_detail,
    )


def _content_resolution(
    source: MappingDocument,
    purchase_orders: tuple[MappingPO, ...],
    remaining_qty: dict[UUID, dict[int, Decimal]],
    remaining_grn_qty: dict[UUID, dict[int, Decimal]],
    remaining_balance: dict[UUID, Decimal],
    config: MappingConfig,
    prior_links: tuple[ProposedLink, ...] = (),
) -> LinkResolution:
    if source.document.header.doc_type is DocType.INVOICE:
        return _content_invoice_resolution(
            source,
            purchase_orders,
            remaining_qty,
            remaining_grn_qty,
            remaining_balance,
            config,
            prior_links,
        )
    eligible: list[MappingPO] = []
    for po in purchase_orders:
        if (
            source.vendor_id is not None
            and po.vendor_id == source.vendor_id
            and po.document_id not in source.rejected_po_ids
        ):
            eligible.append(po)
    scored = []
    for po in eligible:
        score, aligned, signals, missing, *_ = _content_breakdown(
            source, po, _grn_support(po, source), config
        )
        if signals.get("reference") or signals.get("part"):
            scored.append((po, score, aligned, signals, missing))
    scored.sort(key=lambda item: (-item[1], str(item[0].document_id)))
    if not scored:
        return _resolution(source, reason_code=ReasonCode.NO_PO_LINK)
    best_po, best_score, best_alignment, _best_signals, _best_missing = scored[0]
    runner_score = scored[1][1] if len(scored) > 1 else _ZERO
    lead = best_score - runner_score if len(scored) > 1 else _ONE
    candidates = tuple(
        LinkCandidate(
            po_document_id=po.document_id,
            po_no=_doc_no(po.document) or "",
            score=score,
            evidence=_candidate_evidence(source, po, score, signals, missing, (), config),
            signals=signals,
            missing_signals=missing,
        )
        for po, score, _alignment, signals, missing in scored[:3]
    )
    if best_score < config.step3_min_score or lead < config.step3_min_lead:
        return _resolution(
            source,
            candidates=candidates,
            reason_code=ReasonCode.NO_PO_LINK,
            reason_detail="CONTENT_AMBIGUOUS"
            if best_score >= config.step3_min_score
            else "CONTENT_BELOW_THRESHOLD",
        )
    confidence = min(best_score, Decimal("0.90"))
    links = _links_for_matches(
        source,
        ((best_po, LinkLevel.HEADER, None, "Content inference; operator confirmation required."),),
        method=LinkMethod.CONTENT,
        confidence=confidence,
        config=config,
    )
    invoice_lines = {line.line_no: line for line in source.document.lines}
    for invoice_no, po_line_no, _ in best_alignment:
        quantity = _line_quantity(invoice_lines[invoice_no]) or _ZERO
        remaining_grn_qty[best_po.document_id][po_line_no] = (
            remaining_grn_qty[best_po.document_id].get(po_line_no, _ZERO) + quantity
        )
    return _resolution(source, links, candidates=candidates)


def rank_po_candidates(
    source: MappingDocument,
    purchase_orders: tuple[MappingPO, ...] | list[MappingPO],
    config: MappingConfig | None = None,
    *,
    limit: int,
) -> tuple[LinkCandidate, ...]:
    """Rank same-vendor candidates by explainable identity score, best first.

    Fully billed candidates stay visible so a duplicate or over-billing can be
    explained by the separate open-quantity check. Amount variance never filters
    candidate retrieval.
    """

    if limit < 1:
        raise ValueError("candidate limit must be at least 1")
    selected_config = config or MappingConfig()
    if source.vendor_id is None:
        return ()
    scored: list[tuple[MappingPO, Decimal, dict[str, Decimal], tuple[str, ...]]] = []
    for po in purchase_orders:
        if po.vendor_id == source.vendor_id and po.document_id not in source.rejected_po_ids:
            score, _aligned, signals, missing, *_ = _content_breakdown(
                source, po, _grn_support(po, source), selected_config
            )
            if signals.get("reference") or signals.get("part"):
                scored.append((po, score, signals, missing))
    scored.sort(key=lambda item: (-item[1], str(item[0].document_id)))
    return tuple(
        LinkCandidate(
            po_document_id=po.document_id,
            po_no=_doc_no(po.document) or "",
            score=score,
            evidence=_candidate_evidence(source, po, score, signals, missing, (), selected_config),
            signals=signals,
            missing_signals=missing,
        )
        for po, score, signals, missing in scored[:limit]
    )


def resolve_links(
    documents: tuple[MappingDocument, ...] | list[MappingDocument],
    purchase_orders: tuple[MappingPO, ...] | list[MappingPO],
    config: MappingConfig | None = None,
) -> tuple[LinkResolution, ...]:
    """Resolve verified anchors, then period-wide explainable matching suggestions."""

    selected_config = config or MappingConfig()
    sorted_documents = tuple(
        sorted(
            documents,
            key=lambda item: (
                item.natural_key or str(item.document_id),
                str(item.document_id),
            ),
        )
    )
    sorted_pos = tuple(sorted(purchase_orders, key=lambda item: str(item.document_id)))
    po_ids = [po.document_id for po in sorted_pos]
    sorted_documents, verification_updates = _system_check_reference_fields(
        sorted_documents, sorted_pos, selected_config
    )
    document_ids = [document.document_id for document in sorted_documents]
    if len(po_ids) != len(set(po_ids)) or len(document_ids) != len(set(document_ids)):
        raise ValueError("mapping snapshots must have unique document IDs")

    phase_a: dict[UUID, LinkResolution] = {}
    last_issues: dict[UUID, tuple[ReasonCode | None, str | None]] = {}
    for source in sorted_documents:
        links, issue, detail = _labeled_resolution(source, sorted_pos, selected_config)
        if not links and len(_unique_header_po_refs(source.document)) <= 1:
            reverse_links, reverse_issue, reverse_detail = _reverse_matches(
                source, sorted_pos, selected_config
            )
            links = reverse_links
            issue = reverse_issue or issue
            detail = reverse_detail or detail
        phase_a[source.document_id] = _resolution(source, links)
        last_issues[source.document_id] = (issue, detail)

    for source in sorted_documents:
        if phase_a[source.document_id].links or (
            last_issues[source.document_id][0] is ReasonCode.VENDOR_MISMATCH
            or (
                last_issues[source.document_id][0] is ReasonCode.NO_PO_LINK
                and last_issues[source.document_id][1] is not None
            )
            or len(_unique_header_po_refs(source.document)) > 1
        ):
            continue
        links, issue, detail = _via_grn_resolution(
            source, sorted_documents, sorted_pos, phase_a, selected_config
        )
        if links:
            phase_a[source.document_id] = _resolution(source, links)
        elif issue is not None:
            last_issues[source.document_id] = (issue, detail)

    sorted_documents, grn_verification_updates = _system_check_invoice_grn_references(
        sorted_documents, sorted_pos, phase_a, selected_config
    )
    verification_updates = (*verification_updates, *grn_verification_updates)
    sources_rechecked = {item.document_id for item in grn_verification_updates}
    if sources_rechecked:
        for source in sorted_documents:
            if (
                source.document_id not in sources_rechecked
                or source.document.header.doc_type is not DocType.INVOICE
            ):
                continue
            links, issue, detail = _via_grn_resolution(
                source, sorted_documents, sorted_pos, phase_a, selected_config
            )
            prior = phase_a[source.document_id]
            retained = tuple(
                link
                for link in prior.links
                if not (
                    link.method is LinkMethod.VIA_GRN
                    and link.link_type in {LinkType.INV_GRN, LinkType.INV_PO}
                )
            )
            combined = list(retained)
            for link in links:
                if not any(
                    existing.link_type is link.link_type
                    and existing.to_document_id == link.to_document_id
                    and existing.level is link.level
                    and existing.line_no == link.line_no
                    for existing in combined
                ):
                    combined.append(link)
            phase_a[source.document_id] = _resolution(source, tuple(combined))
            if combined:
                last_issues[source.document_id] = (None, None)
            elif issue is not None:
                last_issues[source.document_id] = (issue, detail)

    for source in sorted_documents:
        if phase_a[source.document_id].links or (
            last_issues[source.document_id][0] is ReasonCode.VENDOR_MISMATCH
            or (
                last_issues[source.document_id][0] is ReasonCode.NO_PO_LINK
                and last_issues[source.document_id][1] is not None
            )
        ):
            continue
        links, issue = _manual_resolution(source, sorted_pos, selected_config)
        if links:
            phase_a[source.document_id] = _resolution(source, links)
        elif issue is not None:
            last_issues[source.document_id] = (issue, None)

    # Typed handwritten references corroborated at header level: suggestions only.
    for source in sorted_documents:
        prior = phase_a[source.document_id]
        handwritten = _handwritten_header_links(
            source, sorted_documents, sorted_pos, prior.links, selected_config
        )
        if handwritten:
            phase_a[source.document_id] = _resolution(source, (*prior.links, *handwritten))

    po_by_id = {po.document_id: po for po in sorted_pos}
    remaining_qty = {
        po.document_id: {line_no: value for line_no, value in po.open_qty_by_line.items()}
        for po in sorted_pos
    }
    remaining_grn_qty = {po.document_id: dict(_grn_support(po, None).items()) for po in sorted_pos}
    remaining_open_grn_qty = {
        po.document_id: {line_no: value for line_no, value in po.open_grn_qty_by_line.items()}
        for po in sorted_pos
    }
    remaining_balance = {po.document_id: po.open_balance for po in sorted_pos}
    for source in sorted_documents:
        if source.document.header.doc_type is not DocType.INVOICE:
            continue
        source_links = tuple(
            link
            for link in phase_a[source.document_id].links
            if link.state in {LinkState.AUTO, LinkState.CONFIRMED}
            and link.link_type is LinkType.INV_PO
        )
        header_targets = {
            link.to_document_id
            for link in source_links
            if link.level is LinkLevel.HEADER and link.to_document_id in po_by_id
        }
        line_targets: dict[UUID, set[int]] = {}
        for link in source_links:
            if (
                link.level is LinkLevel.LINE
                and link.line_no is not None
                and link.to_document_id in po_by_id
            ):
                line_targets.setdefault(link.to_document_id, set()).add(link.line_no)
        if not line_targets and len(header_targets) == 1:
            _reserve_invoice_lines(
                source,
                next(iter(header_targets)),
                po_by_id,
                remaining_qty,
                remaining_balance,
                selected_config,
                remaining_open_grn_qty=remaining_open_grn_qty,
            )
        for po_id, line_numbers in line_targets.items():
            _reserve_invoice_lines(
                source,
                po_id,
                po_by_id,
                remaining_qty,
                remaining_balance,
                selected_config,
                line_numbers,
                remaining_open_grn_qty=remaining_open_grn_qty,
            )

    output = dict(phase_a)
    grn_dates: dict[UUID, list[date]] = {}
    for source in sorted_documents:
        if source.document.header.doc_type is not DocType.GRN:
            continue
        grn_date = _header_date(source.document)
        if grn_date is None:
            continue
        for link in phase_a[source.document_id].links:
            if link.link_type is LinkType.GRN_PO and link.to_document_id in po_by_id:
                grn_dates.setdefault(link.to_document_id, []).append(grn_date)

    invoice_sources_list: list[MappingDocument] = []
    for source in sorted_documents:
        if source.document.header.doc_type is not DocType.INVOICE:
            continue
        if last_issues[source.document_id][0] is ReasonCode.VENDOR_MISMATCH or (
            last_issues[source.document_id][0] is ReasonCode.NO_PO_LINK
            and last_issues[source.document_id][1] is not None
        ):
            continue
        prior_invoice_po = tuple(
            link for link in phase_a[source.document_id].links if link.link_type is LinkType.INV_PO
        )
        if not prior_invoice_po:
            invoice_sources_list.append(source)
            continue
        if any(link.level is LinkLevel.HEADER for link in prior_invoice_po):
            continue
        linked_lines = {link.line_no for link in prior_invoice_po if link.line_no is not None}
        unresolved_lines = tuple(
            line for line in source.document.lines if line.line_no not in linked_lines
        )
        if unresolved_lines:
            invoice_sources_list.append(
                source.model_copy(
                    update={
                        "document": source.document.model_copy(update={"lines": unresolved_lines})
                    }
                )
            )
    invoice_sources = tuple(invoice_sources_list)
    period_pos = tuple(
        po.model_copy(
            update={
                "open_qty_by_line": remaining_qty[po.document_id],
                "open_grn_qty_by_line": remaining_open_grn_qty[po.document_id],
                "open_balance": remaining_balance[po.document_id],
            }
        )
        for po in sorted_pos
    )
    period_results = _period_invoice_resolutions(
        invoice_sources,
        period_pos,
        selected_config,
        grn_dates_by_po={key: tuple(sorted(values)) for key, values in grn_dates.items()},
    )
    source_by_id = {source.document_id: source for source in sorted_documents}
    for source_id, result in period_results.items():
        prior = phase_a[source_id]
        if prior.links:
            output[source_id] = _resolution(
                source_by_id[source_id],
                (*prior.links, *result.links),
                candidates=result.candidates,
                reason_code=result.reason_code,
                reason_detail=result.reason_detail,
            )
        else:
            output[source_id] = result

    for source in sorted_documents:
        if (
            source.document.header.doc_type is not DocType.GRN
            or phase_a[source.document_id].links
            or last_issues[source.document_id][0] is ReasonCode.VENDOR_MISMATCH
            or (
                last_issues[source.document_id][0] is ReasonCode.NO_PO_LINK
                and last_issues[source.document_id][1] is not None
            )
        ):
            continue
        output[source.document_id] = _content_resolution(
            source,
            sorted_pos,
            remaining_qty,
            remaining_grn_qty,
            remaining_balance,
            selected_config,
        )

    updates_by_source: dict[UUID, list[FieldVerificationUpdate]] = {}
    for update in verification_updates:
        updates_by_source.setdefault(update.document_id, []).append(update)

    final: list[LinkResolution] = []
    for source in sorted_documents:
        result = output[source.document_id]
        if source.document.header.doc_type is DocType.INVOICE:
            dated_links: list[ProposedLink] = []
            for link in result.links:
                po = (
                    po_by_id.get(link.to_document_id) if link.link_type is LinkType.INV_PO else None
                )
                if (
                    po is None
                    or link.state is LinkState.CONFIRMED
                    or "date_check=" in link.evidence
                ):
                    dated_links.append(link)
                    continue
                date_evidence = _date_check_evidence(source, po, selected_config)
                dated_links.append(
                    link.model_copy(
                        update={"evidence": f"{link.evidence}; {'; '.join(date_evidence)}"}
                    )
                )
            result = result.model_copy(update={"links": tuple(dated_links)})
        issue, detail = last_issues[source.document_id]
        reason_detail = result.reason_detail or detail
        reference_text = result.reference_text
        if issue is ReasonCode.NO_PO_LINK and detail:
            reason_detail = "PO_NOT_IN_POOL"
            reference_text = detail
        final.append(
            result.model_copy(
                update={
                    "reason_code": issue or result.reason_code,
                    "reason_detail": reason_detail,
                    "reference_text": reference_text,
                    "field_verifications": tuple(updates_by_source.get(source.document_id, ())),
                }
            )
        )
    return tuple(final)
