"""Pure, deterministic PO linking through the five-step anchor ladder."""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from reconbench_core.align import (
    AlignmentConfig,
    AlignmentWeights,
    align_lines,
)
from reconbench_core.extraction.pages import split_unconfirmed
from reconbench_core.normalize import canonicalize_document_number, parse_decimal
from reconbench_core.schema import (
    AlignmentState,
    DecimalValue,
    DocType,
    Document,
    FrozenModel,
    GRNHeader,
    InvoiceHeader,
    LinkLevel,
    LinkMethod,
    LinkState,
    LinkType,
    ReasonCode,
    ResolutionOutcome,
)

_ONE = Decimal("1")
_ZERO = Decimal("0")
_FOUR_PLACES = Decimal("0.0001")
MANUAL_SELECTION_EVIDENCE = "Manually selected by the operator"
_CONTENT_WEIGHTS = (
    Decimal("0.40"),
    Decimal("0.20"),
    Decimal("0.15"),
    Decimal("0.10"),
    Decimal("0.10"),
    Decimal("0.05"),
)


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
    alignment: AlignmentConfig = Field(default_factory=_default_alignment)

    @model_validator(mode="after")
    def _validate_thresholds(self) -> Self:
        if not Decimal("0") <= self.link_auto_accept <= Decimal("1"):
            raise ValueError("link auto-accept threshold must be between 0 and 1")
        if not Decimal("0") <= self.step3_min_score <= Decimal("0.90"):
            raise ValueError("content threshold must be between 0 and 0.90")
        if not Decimal("0") <= self.step3_min_lead <= Decimal("1"):
            raise ValueError("content lead threshold must be between 0 and 1")
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


def _doc_no(document: Document) -> str | None:
    field = document.header.doc_no
    return field.value if field is not None else None


def _canonical(value: str | None) -> str:
    return canonicalize_document_number(value or "")


def _last_numeric_run(value: str) -> str | None:
    matches = re.findall(r"\d+", value)
    return matches[-1].lstrip("0") or "0" if matches else None


def _without_leading_label(value: str) -> str:
    return re.sub(r"^(?:PO|ORDER|PUR)", "", _canonical(value))


def _po_matches(
    reference: str,
    purchase_orders: tuple[MappingPO, ...],
    vendor_id: UUID | None,
) -> tuple[tuple[MappingPO, ...], bool]:
    canonical_reference = _canonical(reference)
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
            held or split_unconfirmed(source.document) or split_unconfirmed(po.document)
        ):
            state = LinkState.SUGGESTED
            evidence = f"{evidence}; file split not confirmed"
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
        if missing:
            return (), ReasonCode.NO_PO_LINK, missing[0]
    elif header_refs:
        reference = header_refs[0]
        matches, vendor_mismatch = _po_matches(reference, purchase_orders, source.vendor_id)
        if vendor_mismatch:
            return (), ReasonCode.VENDOR_MISMATCH, reference
        eligible = tuple(po for po in matches if po.document_id not in source.rejected_po_ids)
        if not matches:
            return (), ReasonCode.NO_PO_LINK, reference
        if source.document.header.doc_type is DocType.INVOICE:
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
        elif eligible:
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
        matches, vendor_mismatch = _po_matches(reference, purchase_orders, source.vendor_id)
        if vendor_mismatch:
            mismatched.append(reference)
            continue
        eligible = tuple(po for po in matches if po.document_id not in source.rejected_po_ids)
        if not eligible:
            if not matches:
                missing.append(reference)
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
        *_flatten(header.extras),
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
    ignored = {
        _canonical(_doc_no(source.document)),
        _canonical(header.vendor_gstin.value if header.vendor_gstin else None),
        _canonical(
            header.e_way_bill_no.value
            if isinstance(header, InvoiceHeader) and header.e_way_bill_no
            else None
        ),
        _canonical(header.irn.value if isinstance(header, InvoiceHeader) and header.irn else None),
    }
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
            matches, vendor_mismatch = _po_matches(reference, purchase_orders, source.vendor_id)
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
    invoice_references = set(invoice_header.grn_refs.value if invoice_header.grn_refs else ())
    if invoice_header.challan_no is not None:
        invoice_references.add(invoice_header.challan_no.value)
    grn_identifiers = {
        value
        for value in (
            _doc_no(grn.document),
            grn_header.challan_no.value if grn_header.challan_no else None,
        )
        if value
    }
    if {_canonical(value) for value in invoice_references} & {
        _canonical(value) for value in grn_identifiers
    }:
        return True
    invoice_no = _doc_no(invoice.document)
    return bool(
        invoice_no
        and grn_header.supplier_invoice_no
        and _canonical(invoice_no) == _canonical(grn_header.supplier_invoice_no.value)
    )


def _grn_matches_invoice(invoice: MappingDocument, grn: MappingDocument) -> bool:
    """Require a resolved, identical supplier before accepting a GRN reference."""
    if invoice.vendor_id is None or grn.vendor_id is None or invoice.vendor_id != grn.vendor_id:
        return False
    return _grn_reference_matches_invoice(invoice, grn)


def _via_grn_resolution(
    source: MappingDocument,
    documents: tuple[MappingDocument, ...],
    purchase_orders: tuple[MappingPO, ...],
    phase_a: dict[UUID, LinkResolution],
    config: MappingConfig,
) -> tuple[tuple[ProposedLink, ...], ReasonCode | None, str | None]:
    matched_grns: list[MappingDocument] = []
    mismatched: list[str] = []
    for peer in documents:
        if (
            peer.document.header.doc_type is DocType.GRN
            and source.document.header.doc_type is DocType.INVOICE
            and _grn_reference_matches_invoice(source, peer)
        ):
            if (
                source.vendor_id is None
                or peer.vendor_id is None
                or source.vendor_id != peer.vendor_id
            ):
                mismatched.append(_doc_no(peer.document) or "GRN supplier")
            else:
                matched_grns.append(peer)
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
                matched_grns.append(source)

    matches: list[tuple[MappingPO, LinkLevel, int | None, str]] = []
    for grn in sorted(matched_grns, key=lambda item: str(item.document_id)):
        grn_refs, _, _ = _header_refs(grn.document)
        grn_number = _doc_no(grn.document) or "GRN"
        resolution = phase_a.get(grn.document_id)
        linked_ids = {link.to_document_id for link in resolution.links} if resolution else set()
        for reference in grn_refs:
            grn_pos, vendor_mismatch = _po_matches(reference, purchase_orders, source.vendor_id)
            if vendor_mismatch:
                mismatched.append(reference)
            for po in grn_pos:
                if po.document_id in linked_ids and po.document_id not in source.rejected_po_ids:
                    matches.append(
                        (
                            po,
                            LinkLevel.HEADER,
                            None,
                            f"{grn_number} links this document to PO {reference}",
                        )
                    )
        for po_id in linked_ids:
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
                matches.append(
                    (
                        linked_po,
                        LinkLevel.HEADER,
                        None,
                        f"Linked through GRN {grn_number}",
                    )
                )
    links = _links_for_matches(
        source,
        tuple(matches),
        method=LinkMethod.VIA_GRN,
        confidence=config.confidence_via_grn,
        config=config,
        via_document_id=matched_grns[0].document_id if matched_grns else None,
        held=any(split_unconfirmed(grn.document) for grn in matched_grns),
    )
    if links:
        return links, None, None
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


def _content_score(
    source: MappingDocument,
    po: MappingPO,
    open_qty: dict[int, Decimal],
    grn_qty: dict[int, Decimal],
    config: MappingConfig,
) -> tuple[Decimal, tuple[tuple[int, int, Decimal], ...]]:
    alignment = align_lines(
        source.document.lines,
        po.document.lines,
        config.alignment,
    )
    invoice_lines = {line.line_no: line for line in source.document.lines}
    po_lines = {line.line_no: line for line in po.document.lines}
    aligned_rows: list[tuple[int, int, Decimal]] = []
    for match in alignment:
        if (
            match.state is AlignmentState.ALIGNED
            and match.po_line_no is not None
            and match.score is not None
        ):
            aligned_rows.append((match.invoice_line_no, match.po_line_no, match.score))

    invoice_value = sum((_line_amount(line) for line in source.document.lines), _ZERO)
    aligned_value = sum(
        (_line_amount(invoice_lines[invoice_no]) for invoice_no, _, _ in aligned_rows),
        _ZERO,
    )
    if invoice_value > _ZERO:
        line_coverage = min(_ONE, aligned_value / invoice_value)
    elif source.document.lines:
        line_coverage = Decimal(len(aligned_rows)) / Decimal(len(source.document.lines))
    else:
        line_coverage = _ZERO

    total_qty = _ZERO
    feasible_qty = _ZERO
    supported_qty = _ZERO
    rate_scores: list[tuple[Decimal, Decimal]] = []
    for invoice_no, po_no, _ in aligned_rows:
        invoice_line = invoice_lines[invoice_no]
        po_line = po_lines[po_no]
        billed_qty = _line_quantity(invoice_line) or _ZERO
        available_qty = open_qty.get(po_no, _ZERO)
        support_qty = grn_qty.get(po_no, _ZERO)
        total_qty += billed_qty
        feasible_qty += min(billed_qty, available_qty)
        supported_qty += min(billed_qty, support_qty)
        invoice_rate = _line_value(invoice_line, "rate")
        po_rate = _line_value(po_line, "rate")
        if invoice_rate is not None and po_rate is not None:
            denominator = max(abs(po_rate), Decimal("1"))
            agreement = max(_ZERO, _ONE - abs(invoice_rate - po_rate) / denominator)
            rate_scores.append((agreement, max(billed_qty, Decimal("1"))))
    qty_feasible = feasible_qty / total_qty if total_qty > _ZERO else _ZERO
    grn_support = supported_qty / total_qty if total_qty > _ZERO else _ZERO
    rate_agree = (
        sum((score * weight for score, weight in rate_scores), _ZERO)
        / sum((weight for _, weight in rate_scores), _ZERO)
        if rate_scores
        else _ZERO
    )
    invoice_date = _header_date(source.document)
    po_date = _header_date(po.document)
    date_ok = (
        _ONE
        if invoice_date is not None and po_date is not None and po_date <= invoice_date
        else _ZERO
    )
    invoice_header = source.document.header
    po_header = po.document.header
    invoice_buyer = invoice_header.buyer_gstin.value if invoice_header.buyer_gstin else None
    po_buyer = po_header.buyer_gstin.value if po_header.buyer_gstin else None
    buyer_match = _ONE if invoice_buyer and po_buyer and invoice_buyer == po_buyer else _ZERO
    score = sum(
        (
            _CONTENT_WEIGHTS[0] * line_coverage,
            _CONTENT_WEIGHTS[1] * qty_feasible,
            _CONTENT_WEIGHTS[2] * rate_agree,
            _CONTENT_WEIGHTS[3] * date_ok,
            _CONTENT_WEIGHTS[4] * grn_support,
            _CONTENT_WEIGHTS[5] * buyer_match,
        ),
        _ZERO,
    ).quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)
    return score, tuple(aligned_rows)


def _reserve_invoice_lines(
    source: MappingDocument,
    po_id: UUID,
    po_by_id: dict[UUID, MappingPO],
    remaining_qty: dict[UUID, dict[int, Decimal]],
    remaining_balance: dict[UUID, Decimal],
    config: MappingConfig,
    line_numbers: set[int] | None = None,
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
    invoice_date = _header_date(source.document)
    header_refs = _unique_header_po_refs(source.document)
    candidate_ids: set[UUID] | None = None
    if len(header_refs) > 1:
        candidate_ids = set()
        for reference in header_refs:
            matches, vendor_mismatch = _po_matches(reference, purchase_orders, source.vendor_id)
            if vendor_mismatch:
                return _resolution(
                    source,
                    prior_links,
                    reason_code=ReasonCode.VENDOR_MISMATCH,
                    reference_text=reference,
                )
            if not matches:
                return _resolution(
                    source,
                    prior_links,
                    reason_code=ReasonCode.NO_PO_LINK,
                    reason_detail="PO_NOT_IN_POOL",
                    reference_text=reference,
                )
            candidate_ids.update(po.document_id for po in matches)

    eligible = tuple(
        po
        for po in purchase_orders
        if source.vendor_id is not None
        and po.vendor_id == source.vendor_id
        and remaining_balance.get(po.document_id, _ZERO) > _ZERO
        and invoice_date is not None
        and (po_date := _header_date(po.document)) is not None
        and po_date <= invoice_date
        and po.document_id not in source.rejected_po_ids
        and (candidate_ids is None or po.document_id in candidate_ids)
    )
    if not source.document.lines:
        return _resolution(source, prior_links, reason_code=ReasonCode.NO_PO_LINK)

    linked_lines = {
        link.line_no for link in prior_links if link.level is LinkLevel.LINE and link.line_no
    }
    created_links: list[ProposedLink] = []
    unresolved_candidates: list[LinkCandidate] = []
    invoice_lines = {line.line_no: line for line in source.document.lines}
    for line in sorted(source.document.lines, key=lambda item: item.line_no):
        if line.line_no in linked_lines:
            continue
        single_line_source = source.model_copy(
            update={"document": source.document.model_copy(update={"lines": (line,)})}
        )
        available_pos = tuple(
            po for po in eligible if remaining_balance.get(po.document_id, _ZERO) > _ZERO
        )
        scored = [
            (
                po,
                *_content_score(
                    single_line_source,
                    po,
                    remaining_qty[po.document_id],
                    remaining_grn_qty[po.document_id],
                    config,
                ),
            )
            for po in available_pos
        ]
        scored.sort(key=lambda item: (-item[1], str(item[0].document_id)))
        if not scored:
            continue
        best_po, best_score, best_alignment = scored[0]
        runner_score = scored[1][1] if len(scored) > 1 else _ZERO
        lead = best_score - runner_score if len(scored) > 1 else _ONE
        line_candidates = tuple(
            LinkCandidate(
                po_document_id=po.document_id,
                po_no=_doc_no(po.document) or "",
                score=score,
                evidence=(
                    "Line-level content score over item, quantity, rate, date, GRN, and buyer "
                    "signals."
                ),
                line_no=line.line_no,
            )
            for po, score, _ in scored[:3]
        )
        if best_score < config.step3_min_score or lead < config.step3_min_lead:
            unresolved_candidates.extend(line_candidates)
            continue

        confidence = min(best_score, Decimal("0.90"))
        created_links.extend(
            _links_for_matches(
                source,
                (
                    (
                        best_po,
                        LinkLevel.LINE,
                        line.line_no,
                        (
                            f"Content inference for invoice line {line.line_no}; "
                            "operator confirmation required."
                        ),
                    ),
                ),
                method=LinkMethod.CONTENT,
                confidence=confidence,
                config=config,
            )
        )
        for invoice_no, po_line_no, _ in best_alignment:
            invoice_line = invoice_lines[invoice_no]
            quantity = _line_quantity(invoice_line) or _ZERO
            remaining_qty[best_po.document_id][po_line_no] = max(
                _ZERO, remaining_qty[best_po.document_id].get(po_line_no, _ZERO) - quantity
            )
            amount = _line_amount(invoice_line)
            remaining_balance[best_po.document_id] = max(
                _ZERO, remaining_balance.get(best_po.document_id, _ZERO) - amount
            )

    all_links = (*prior_links, *created_links)
    reason_detail = None
    if unresolved_candidates:
        reason_detail = (
            "CONTENT_AMBIGUOUS"
            if any(candidate.score >= config.step3_min_score for candidate in unresolved_candidates)
            else "CONTENT_BELOW_THRESHOLD"
        )
    return _resolution(
        source,
        all_links,
        candidates=tuple(unresolved_candidates),
        reason_code=ReasonCode.NO_PO_LINK if reason_detail else None,
        reason_detail=reason_detail,
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
    invoice_date = _header_date(source.document)
    eligible: list[MappingPO] = []
    for po in purchase_orders:
        po_date = _header_date(po.document)
        if (
            source.vendor_id is not None
            and po.vendor_id == source.vendor_id
            and remaining_balance.get(po.document_id, _ZERO) > _ZERO
            and invoice_date is not None
            and po_date is not None
            and po_date <= invoice_date
            and po.document_id not in source.rejected_po_ids
        ):
            eligible.append(po)
    scored = [
        (
            po,
            *_content_score(
                source,
                po,
                remaining_qty[po.document_id],
                remaining_grn_qty[po.document_id],
                config,
            ),
        )
        for po in eligible
    ]
    scored.sort(key=lambda item: (-item[1], str(item[0].document_id)))
    if not scored:
        return _resolution(source, reason_code=ReasonCode.NO_PO_LINK)
    best_po, best_score, best_alignment = scored[0]
    runner_score = scored[1][1] if len(scored) > 1 else _ZERO
    lead = best_score - runner_score if len(scored) > 1 else _ONE
    candidates = tuple(
        LinkCandidate(
            po_document_id=po.document_id,
            po_no=_doc_no(po.document) or "",
            score=score,
            evidence="Content score over item, quantity, rate, date, GRN, and buyer signals.",
        )
        for po, score, _ in scored[:3]
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
    """Rank eligible same-vendor POs by whole-document content score, best first.

    Uses each PO's phase-A open-balance snapshot without in-run reservations, so it
    never changes :func:`resolve_links` output. Eligibility matches content inference:
    same vendor, open balance, PO dated on or before the source, not rejected by a person.
    """

    if limit < 1:
        raise ValueError("candidate limit must be at least 1")
    selected_config = config or MappingConfig()
    source_date = _header_date(source.document)
    if source.vendor_id is None or source_date is None:
        return ()
    scored: list[tuple[MappingPO, Decimal]] = []
    for po in purchase_orders:
        po_date = _header_date(po.document)
        if (
            po.vendor_id == source.vendor_id
            and po.open_balance > _ZERO
            and po_date is not None
            and po_date <= source_date
            and po.document_id not in source.rejected_po_ids
        ):
            score, _ = _content_score(
                source,
                po,
                dict(po.open_qty_by_line),
                dict(po.grn_support_qty_by_line),
                selected_config,
            )
            scored.append((po, score))
    scored.sort(key=lambda item: (-item[1], str(item[0].document_id)))
    return tuple(
        LinkCandidate(
            po_document_id=po.document_id,
            po_no=_doc_no(po.document) or "",
            score=score,
            evidence="Content score over item, quantity, rate, date, GRN, and buyer signals.",
        )
        for po, score in scored[:limit]
    )


def resolve_links(
    documents: tuple[MappingDocument, ...] | list[MappingDocument],
    purchase_orders: tuple[MappingPO, ...] | list[MappingPO],
    config: MappingConfig | None = None,
) -> tuple[LinkResolution, ...]:
    """Resolve phase-A anchors for all sources, then content suggestions in stable order."""

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

    po_by_id = {po.document_id: po for po in sorted_pos}
    remaining_qty = {
        po.document_id: {line_no: value for line_no, value in po.open_qty_by_line.items()}
        for po in sorted_pos
    }
    remaining_grn_qty = {
        po.document_id: {line_no: value for line_no, value in po.grn_support_qty_by_line.items()}
        for po in sorted_pos
    }
    remaining_balance = {po.document_id: po.open_balance for po in sorted_pos}
    for source in sorted_documents:
        if source.document.header.doc_type is not DocType.INVOICE:
            continue
        source_links = phase_a[source.document_id].links
        header_targets = {
            link.to_document_id for link in source_links if link.level is LinkLevel.HEADER
        }
        line_targets: dict[UUID, set[int]] = {}
        for link in source_links:
            if link.level is LinkLevel.LINE and link.line_no is not None:
                line_targets.setdefault(link.to_document_id, set()).add(link.line_no)
        if not line_targets and len(header_targets) == 1:
            _reserve_invoice_lines(
                source,
                next(iter(header_targets)),
                po_by_id,
                remaining_qty,
                remaining_balance,
                selected_config,
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
            )

    output = dict(phase_a)
    content_order = sorted(
        (
            source
            for source in sorted_documents
            if source.document.header.doc_type in {DocType.INVOICE, DocType.GRN}
            and (
                not phase_a[source.document_id].links
                or (
                    source.document.header.doc_type is DocType.INVOICE
                    and all(
                        link.level is LinkLevel.LINE for link in phase_a[source.document_id].links
                    )
                )
            )
            and not (
                last_issues[source.document_id][0] is ReasonCode.VENDOR_MISMATCH
                or (
                    last_issues[source.document_id][0] is ReasonCode.NO_PO_LINK
                    and last_issues[source.document_id][1] is not None
                )
            )
        ),
        key=lambda item: (
            _header_date(item.document) or date.max,
            _canonical(_doc_no(item.document)),
            item.natural_key or str(item.document_id),
            str(item.document_id),
        ),
    )
    for source in content_order:
        output[source.document_id] = _content_resolution(
            source,
            sorted_pos,
            remaining_qty,
            remaining_grn_qty,
            remaining_balance,
            selected_config,
            prior_links=phase_a[source.document_id].links,
        )

    final: list[LinkResolution] = []
    for source in sorted_documents:
        result = output[source.document_id]
        if result.links:
            final.append(result)
            continue
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
                }
            )
        )
    return tuple(final)
