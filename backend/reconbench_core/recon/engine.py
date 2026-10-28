"""Pure, deterministic reconciliation of a frozen document/link snapshot."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from reconbench_core.align import AlignmentResult, align_lines
from reconbench_core.extraction.pages import split_unconfirmed
from reconbench_core.normalize import (
    canonicalize_document_number,
    financial_year,
    normalize_item_text,
    normalize_uom,
    validate_gstin,
)
from reconbench_core.recon.registry import check_definition, rules_hash
from reconbench_core.recon.types import (
    ReconciliationConfig,
    ReconciliationDocument,
    ReconciliationLink,
    ReconciliationSnapshot,
    ToleranceRule,
    ToleranceValues,
)
from reconbench_core.schema import (
    AlignmentState,
    CaseResult,
    CaseStatus,
    CheckOutcome,
    CheckResult,
    DocLine,
    DocType,
    Document,
    FieldOrigin,
    FieldSource,
    FieldValue,
    FieldVerification,
    InvoiceHeader,
    LineResult,
    LineType,
    LinkLevel,
    LinkMethod,
    LinkState,
    LinkSummary,
    Manifest,
    ManifestDocument,
    POHeader,
    POStatus,
    ReasonCode,
    ReasonLevel,
    Tolerance,
    ToleranceMode,
    ToleranceSource,
)
from reconbench_core.version import ENGINE_VERSION

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")
_ACTIVE_LINK_STATES = {LinkState.AUTO, LinkState.CONFIRMED, LinkState.SUGGESTED}
_DUPLICATE_NUMBER_LOOKALIKE_FOLD = str.maketrans(
    {"O": "0", "I": "1", "L": "1", "|": "1", "S": "5", "B": "8", "Z": "2"}
)


@dataclass
class _LineWork:
    invoice: ReconciliationDocument
    line: DocLine
    link: ReconciliationLink | None
    po: ReconciliationDocument | None
    po_line: DocLine | None
    alignment: AlignmentResult | None
    grn_ids: set[UUID] = field(default_factory=set)
    checks: list[CheckResult] = field(default_factory=list)
    reasons: list[ReasonCode] = field(default_factory=list)
    status: CaseStatus = CaseStatus.MATCHED
    po_cap: Decimal | None = None
    billed_before: Decimal | None = None
    billed_qty: Decimal | None = None
    grn_accepted: Decimal | None = None
    grn_rejected: Decimal | None = None
    eligible: Decimal | None = None
    balance_after: Decimal | None = None
    shortfall: Decimal | None = None
    po_rate: Decimal | None = None
    invoice_rate: Decimal | None = None
    payable_rate: Decimal | None = None
    line_taxable: Decimal = _ZERO
    payable_taxable: Decimal = _ZERO
    at_risk: Decimal = _ZERO
    approved_charge: bool = False


def _value[ValueT](field_value: FieldValue[ValueT] | None) -> ValueT | None:
    return None if field_value is None else field_value.value


def _decimal(value: object | None) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _text(value: object | None) -> str | None:
    return value if isinstance(value, str) else None


def _field_decimal(line: DocLine, field_name: str) -> Decimal | None:
    return _decimal(_value(getattr(line, field_name)))


def _line_qty(line: DocLine) -> Decimal | None:
    return _field_decimal(line, "quantity")


def _line_amount(line: DocLine) -> Decimal | None:
    amount = _field_decimal(line, "amount")
    if amount is not None:
        return amount
    quantity = _line_qty(line)
    rate = _field_decimal(line, "rate")
    return quantity * rate if quantity is not None and rate is not None else None


def _doc_date(document: Document) -> date | None:
    value = _value(document.header.doc_date)
    return value if isinstance(value, date) else None


def _doc_no(document: Document) -> str:
    return _text(_value(document.header.doc_no)) or ""


def _taxable(document: Document) -> Decimal | None:
    return _decimal(_value(document.header.taxable_total))


def _grand_total(document: Document) -> Decimal | None:
    return _decimal(_value(document.header.grand_total))


def _field_values(document: Document) -> tuple[tuple[str, FieldValue[object]], ...]:
    """Every header and line value, named the way a person sees it."""

    values = [
        (name, value)
        for name in type(document.header).model_fields
        if isinstance(value := getattr(document.header, name), FieldValue)
    ]
    values.extend(
        (f"line {line.line_no} {name}", value)
        for line in document.lines
        for name in type(line).model_fields
        if isinstance(value := getattr(line, name), FieldValue)
    )
    return tuple(values)


def _read_by_ai(field: FieldValue[object]) -> bool:
    # The same rule as for references (PD-2a): its origin or its source says AI.
    return field.origin is FieldOrigin.AI or field.source is FieldSource.AI


def _checked_by_a_person(field: FieldValue[object]) -> bool:
    """A value a person verified, or typed in as a correction (PD-2b)."""

    return field.verification is FieldVerification.USER or (
        field.source is FieldSource.MANUAL and not _read_by_ai(field)
    )


def _gate_confidence(document: Document) -> Decimal | None:
    """The extraction gate's confidence: ``Document.confidence`` with a person's work counted.

    A value a person checked counts as certain. A corrected value has no confidence of
    its own, so an invoice whose vendor or lines a person typed in stayed at 0 for ever
    (F2, PD-2b).
    """

    def certain(field: object) -> object:
        if isinstance(field, FieldValue) and _checked_by_a_person(field):
            return field.model_copy(update={"confidence": Decimal("1")})
        return field

    header = document.header.model_copy(
        update={
            name: certain(getattr(document.header, name))
            for name in type(document.header).model_fields
        }
    )
    lines = tuple(
        line.model_copy(
            update={name: certain(getattr(line, name)) for name in type(line).model_fields}
        )
        for line in document.lines
    )
    return document.model_copy(update={"header": header, "lines": lines}).confidence


def _unverified_ai_values(document: Document) -> tuple[str, ...]:
    """The values read by AI that no person has verified, whatever their confidence."""

    return tuple(
        name
        for name, value in _field_values(document)
        if _read_by_ai(value) and value.verification is not FieldVerification.USER
    )


def _duplicate_number_key(value: str) -> str:
    folded = value.upper().translate(_DUPLICATE_NUMBER_LOOKALIKE_FOLD)
    return canonicalize_document_number(folded)


def _line_amounts_fingerprint(document: Document) -> tuple[Decimal, ...] | None:
    amounts = [_line_amount(line) for line in document.lines]
    if not amounts or any(amount is None for amount in amounts):
        return None
    return tuple(sorted(amount for amount in amounts if amount is not None))


def _unique_codes(codes: list[ReasonCode]) -> tuple[ReasonCode, ...]:
    return tuple(sorted(set(codes), key=lambda item: item.value))


def _make_check(
    check_id: str,
    result: CheckOutcome,
    *,
    code: ReasonCode | None = None,
    level: ReasonLevel | None = None,
    expected: Decimal | str | None = None,
    actual: Decimal | str | None = None,
    variance: Decimal | None = None,
    tolerance: Tolerance | None = None,
    evidence: dict[str, object] | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        check_version=check_definition(check_id).version,
        code=code,
        level=level,
        result=result,
        expected=expected,
        actual=actual,
        variance=variance,
        tolerance=tolerance,
        evidence=evidence or {},
    )


def _effective_tolerance(
    rule: ToleranceRule,
    mode: ToleranceMode,
    vendor_id: UUID | None,
    item_key: str | None,
) -> Tolerance:
    source = ToleranceSource.GLOBAL
    values = ToleranceValues(pct=rule.pct, abs=rule.abs)
    vendor_item_key = f"{vendor_id}|{item_key}" if vendor_id is not None and item_key else None
    if vendor_item_key and vendor_item_key in rule.by_vendor_item:
        values = rule.by_vendor_item[vendor_item_key]
        source = ToleranceSource.ITEM
    elif item_key and item_key in rule.by_item:
        values = rule.by_item[item_key]
        source = ToleranceSource.ITEM
    elif vendor_id is not None and str(vendor_id) in rule.by_vendor:
        values = rule.by_vendor[str(vendor_id)]
        source = ToleranceSource.VENDOR
    return Tolerance(pct=values.pct, abs=values.abs, mode=mode, source=source)


def _tolerance_pass(actual: Decimal, expected: Decimal, tolerance: Tolerance) -> bool:
    variance = abs(actual - expected)
    variance_pct = (
        variance / abs(expected) * _HUNDRED
        if expected != _ZERO
        else (_ZERO if variance == _ZERO else _HUNDRED)
    )
    within_abs = variance <= tolerance.abs
    within_pct = variance == _ZERO if expected == _ZERO else variance_pct <= tolerance.pct
    return (
        within_abs or within_pct
        if tolerance.mode is ToleranceMode.OR
        else within_abs and within_pct
    )


def _tolerance_allowance(expected: Decimal, tolerance: Tolerance) -> Decimal:
    by_pct = abs(expected) * tolerance.pct / _HUNDRED
    by_abs = tolerance.abs
    return max(by_pct, by_abs) if tolerance.mode is ToleranceMode.OR else min(by_pct, by_abs)


def _line_item_key(line: DocLine) -> str | None:
    code = _text(_value(line.item_code))
    description = _text(_value(line.description))
    raw = code or description
    return raw.casefold().strip() if raw else None


def _rule(
    config: ReconciliationConfig,
    name: str,
    vendor_id: UUID | None,
    line: DocLine | None,
) -> Tolerance:
    setting = config.tolerances.get(name)
    if setting is None:
        setting = ToleranceRule(pct=Decimal(0), abs=Decimal(0))
    return _effective_tolerance(
        setting,
        config.limit_mode,
        vendor_id,
        _line_item_key(line) if line is not None else None,
    )


def _check_math(
    checks: list[CheckResult],
    reasons: list[ReasonCode],
    check_id: str,
    code: ReasonCode,
    actual: Decimal,
    expected: Decimal,
    tolerance: Tolerance,
) -> bool:
    difference = actual - expected
    passed = _tolerance_pass(actual, expected, tolerance)
    checks.append(
        _make_check(
            check_id,
            CheckOutcome.PASS if passed else CheckOutcome.FAIL,
            code=None if passed else code,
            level=None if passed else ReasonLevel.FAIL,
            expected=expected,
            actual=actual,
            variance=difference,
            tolerance=tolerance,
            evidence={"formula": "absolute or percentage variance per configured limit_mode"},
        )
    )
    if not passed:
        reasons.append(code)
    return passed


def _pan_for(
    record: ReconciliationDocument,
    snapshot: ReconciliationSnapshot,
) -> str | None:
    pan = snapshot.vendor_pan_by_id.get(record.vendor_id) if record.vendor_id else None
    if pan:
        return pan.upper()
    gstin = _text(_value(record.document.header.vendor_gstin))
    validation = validate_gstin(gstin) if gstin else None
    return validation.pan if validation is not None and validation.format_valid else None


def _duplicate_map(
    invoices: tuple[ReconciliationDocument, ...],
    snapshot: ReconciliationSnapshot,
    config: ReconciliationConfig,
) -> tuple[dict[UUID, UUID], dict[UUID, UUID]]:
    exact: dict[UUID, UUID] = {}
    suspected: dict[UUID, UUID] = {}
    seen_keys: dict[tuple[str, str, str], UUID] = {}
    seen_irn: dict[str, UUID] = {}
    ordered = sorted(
        invoices,
        key=lambda item: (
            _doc_date(item.document) or date.max,
            canonicalize_document_number(_doc_no(item.document)),
            item.natural_key or str(item.document_id),
            str(item.document_id),
        ),
    )
    for invoice in ordered:
        header = invoice.document.header
        if not isinstance(header, InvoiceHeader):
            continue
        invoice_date = _doc_date(invoice.document)
        canonical_no = canonicalize_document_number(_doc_no(invoice.document))
        pan = _pan_for(invoice, snapshot)
        if invoice_date is not None and canonical_no and pan:
            key = (pan, canonical_no, financial_year(invoice_date))
            original = seen_keys.get(key)
            if original is not None:
                exact[invoice.document_id] = original
            else:
                seen_keys[key] = invoice.document_id
        irn = _text(_value(header.irn))
        if irn:
            original = seen_irn.get(irn.strip().upper())
            if original is not None:
                exact[invoice.document_id] = original
            else:
                seen_irn[irn.strip().upper()] = invoice.document_id

    candidate_history: deque[tuple[date, Decimal, str, tuple[Decimal, ...] | None, UUID]] = deque()
    for invoice in ordered:
        if not isinstance(invoice.document.header, InvoiceHeader):
            continue
        invoice_date = _doc_date(invoice.document)
        invoice_total = _grand_total(invoice.document)
        if invoice_date is None or invoice_total is None:
            continue

        cutoff = invoice_date - timedelta(days=config.duplicate_suspect_days)
        while candidate_history and candidate_history[0][0] < cutoff:
            candidate_history.popleft()

        invoice_no = _duplicate_number_key(_doc_no(invoice.document))
        line_amounts = _line_amounts_fingerprint(invoice.document)
        if invoice.document_id not in exact:
            for _prior_date, prior_total, prior_no, prior_amounts, prior_id in candidate_history:
                if abs(invoice_total - prior_total) > Decimal("1"):
                    continue
                same_number = bool(invoice_no and prior_no and invoice_no == prior_no)
                same_amounts = (
                    line_amounts is not None
                    and prior_amounts is not None
                    and line_amounts == prior_amounts
                )
                if same_number or same_amounts:
                    suspected[invoice.document_id] = prior_id
                    break
        # Exact duplicates remain eligible as prior candidates, matching the
        # original ordered-prefix rule for later invoices.
        candidate_history.append(
            (invoice_date, invoice_total, invoice_no, line_amounts, invoice.document_id)
        )
    return exact, suspected


def _accepted_grn_qty(line: DocLine) -> tuple[Decimal, Decimal, bool]:
    accepted = _field_decimal(line, "accepted_qty")
    received = _field_decimal(line, "received_qty")
    rejected = _field_decimal(line, "rejected_qty") or _ZERO
    if accepted is not None:
        return accepted, rejected, False
    if received is None:
        return _ZERO, rejected, True
    if rejected > _ZERO:
        return max(_ZERO, received - rejected), rejected, False
    return received, rejected, True


def _link_needs_confirming(link: ReconciliationLink, config: ReconciliationConfig) -> bool:
    """A suggested or below-threshold link needs a person; a confirmed link already had one.

    A person's confirmation is the resolution LINK_INFERRED asks for, so a confirmed link
    is never flagged again for the confidence the matcher gave it before the confirmation.
    """

    if link.state is LinkState.CONFIRMED:
        return False
    return link.state is LinkState.SUGGESTED or link.confidence < config.link_auto_accept


def _active_links(snapshot: ReconciliationSnapshot) -> tuple[ReconciliationLink, ...]:
    return tuple(link for link in snapshot.links if link.state in _ACTIVE_LINK_STATES)


def _line_links_for_invoice(
    invoice_id: UUID,
    line_no: int,
    links: tuple[ReconciliationLink, ...],
) -> tuple[ReconciliationLink, ...]:
    invoice_links = tuple(link for link in links if link.from_document_id == invoice_id)
    explicit = tuple(
        link for link in invoice_links if link.level is LinkLevel.LINE and link.line_no == line_no
    )
    return explicit or tuple(link for link in invoice_links if link.level is LinkLevel.HEADER)


def _build_grn_ledger(
    snapshot: ReconciliationSnapshot,
    documents: dict[UUID, ReconciliationDocument],
    po_documents: dict[UUID, ReconciliationDocument],
    links: tuple[ReconciliationLink, ...],
    config: ReconciliationConfig,
) -> tuple[
    dict[tuple[UUID, int], Decimal],
    dict[tuple[UUID, int], Decimal],
    dict[tuple[UUID, int], set[UUID]],
    dict[tuple[UUID, int], bool],
]:
    accepted: dict[tuple[UUID, int], Decimal] = {}
    rejected: dict[tuple[UUID, int], Decimal] = {}
    grn_ids: dict[tuple[UUID, int], set[UUID]] = {}
    fallback_received: dict[tuple[UUID, int], bool] = {}
    for opening in sorted(
        snapshot.opening_balances, key=lambda item: (str(item.po_document_id), item.po_line_no)
    ):
        key = (opening.po_document_id, opening.po_line_no)
        accepted[key] = accepted.get(key, _ZERO) + opening.received_qty
    grn_links = tuple(
        link
        for link in links
        if documents[link.from_document_id].document.header.doc_type is DocType.GRN
        and documents[link.to_document_id].document.header.doc_type is DocType.PO
    )
    # A GRN can hold a header link and line links to the same PO. Each GRN line counts
    # once per PO: its own line link if it has one, else the header link (as on the
    # invoice side), so one receipt is never added to the PO ledger twice.
    links_by_pair: dict[tuple[UUID, UUID], list[ReconciliationLink]] = {}
    for link in grn_links:
        links_by_pair.setdefault((link.from_document_id, link.to_document_id), []).append(link)
    for grn_id, po_id in sorted(links_by_pair, key=lambda pair: (str(pair[0]), str(pair[1]))):
        pair_links = links_by_pair[(grn_id, po_id)]
        grn_record = documents[grn_id]
        po_record = po_documents[po_id]
        line_linked = {item.line_no for item in pair_links if item.level is LinkLevel.LINE}
        has_header = any(item.level is LinkLevel.HEADER for item in pair_links)
        selected_lines = tuple(
            line for line in grn_record.document.lines if line.line_no in line_linked or has_header
        )
        aligned = align_lines(selected_lines, po_record.document.lines, config.alignment)
        grn_line_by_no = {line.line_no: line for line in selected_lines}
        for match in aligned:
            if match.po_line_no is None or match.state not in {
                AlignmentState.ALIGNED,
                AlignmentState.UNSURE,
            }:
                continue
            grn_line = grn_line_by_no.get(match.invoice_line_no)
            if grn_line is None:
                continue
            qty, rejected_qty, assumed = _accepted_grn_qty(grn_line)
            key = (po_id, match.po_line_no)
            accepted[key] = accepted.get(key, _ZERO) + qty
            rejected[key] = rejected.get(key, _ZERO) + rejected_qty
            grn_ids.setdefault(key, set()).add(grn_id)
            if assumed:
                fallback_received[key] = True
    return accepted, rejected, grn_ids, fallback_received


def _invoice_net_rate(line: DocLine) -> Decimal | None:
    quantity = _line_qty(line)
    amount = _line_amount(line)
    if quantity not in (None, _ZERO) and amount is not None:
        return amount / quantity
    return _field_decimal(line, "rate")


def _line_status(checks: tuple[CheckResult, ...], low_confidence: bool) -> CaseStatus:
    if low_confidence or any(item.result is CheckOutcome.REVIEW for item in checks):
        return CaseStatus.NEEDS_REVIEW
    if any(
        item.result is CheckOutcome.FAIL or item.result is CheckOutcome.BLOCK for item in checks
    ):
        return CaseStatus.EXCEPTION
    if any(item.result is CheckOutcome.PENDING for item in checks):
        return CaseStatus.PENDING
    if any(item.result in {CheckOutcome.WARN, CheckOutcome.INFO} for item in checks):
        return CaseStatus.MATCHED_FLAGS
    return CaseStatus.MATCHED


@dataclass(frozen=True)
class _InvoiceFinance:
    expected: Decimal | None
    payable: Decimal
    at_risk: Decimal
    at_risk_gst: Decimal
    payment_due_date: date | None


def _add_work_check(
    work: _LineWork,
    check: CheckResult,
    reason: ReasonCode | None = None,
) -> None:
    work.checks.append(check)
    if reason is not None:
        work.reasons.append(reason)


def _add_case_check(
    invoice: ReconciliationDocument,
    check: CheckResult,
    reason: ReasonCode | None,
    header_checks: dict[UUID, list[CheckResult]],
    case_reasons: dict[UUID, list[ReasonCode]],
) -> None:
    header_checks[invoice.document_id].append(check)
    if reason is not None:
        case_reasons[invoice.document_id].append(reason)


def _payment_due(invoice: Document, po: Document) -> date | None:
    invoice_date = _doc_date(invoice)
    header = po.header
    terms = _text(_value(header.payment_terms)) if isinstance(header, POHeader) else None
    if invoice_date is None or not terms:
        return None
    match = re.search(r"(?:NET\s*)?(\d{1,3})\s*(?:DAYS?|D)?\b", terms, flags=re.I)
    if match is None:
        return None
    return invoice_date + timedelta(days=int(match.group(1)))


def _tax_rate_for_line(work: _LineWork) -> Decimal | None:
    return _field_decimal(work.line, "tax_rate") or (
        _field_decimal(work.po_line, "tax_rate") if work.po_line is not None else None
    )


def _gst_state(value: str | None) -> str | None:
    if not value:
        return None
    result = validate_gstin(value)
    return result.state_code if result.format_valid else None


def _apply_financial_checks(
    snapshot: ReconciliationSnapshot,
    config: ReconciliationConfig,
    invoices: tuple[ReconciliationDocument, ...],
    documents: dict[UUID, ReconciliationDocument],
    works: list[_LineWork],
    header_checks: dict[UUID, list[CheckResult]],
    case_reasons: dict[UUID, list[ReasonCode]],
) -> dict[UUID, _InvoiceFinance]:
    works_by_invoice: dict[UUID, list[_LineWork]] = {}
    for work in works:
        works_by_invoice.setdefault(work.invoice.document_id, []).append(work)
    expected_by_invoice: dict[UUID, Decimal] = {}
    payable_by_invoice: dict[UUID, Decimal] = {}
    risk_by_invoice: dict[UUID, Decimal] = {}
    risk_gst_by_invoice: dict[UUID, Decimal] = {}
    due_dates: dict[UUID, set[date]] = {}
    po_values: dict[UUID, dict[UUID, tuple[ReconciliationDocument, Decimal]]] = {}
    exact_duplicate_ids, _ = _duplicate_map(invoices, snapshot, config)

    for invoice in invoices:
        invoice_works = works_by_invoice.get(invoice.document_id, [])
        expected = _ZERO
        matched_any = False
        checked_pairs: set[UUID] = set()
        header = invoice.document.header
        if not isinstance(header, InvoiceHeader):
            continue
        invoice_currency = _text(_value(header.currency))
        invoice_gstin = _text(_value(header.vendor_gstin))
        gstin_result = validate_gstin(invoice_gstin) if invoice_gstin else None
        if invoice.vendor_id is None:
            _add_case_check(
                invoice,
                _make_check(
                    "s1.vendor_resolved",
                    CheckOutcome.REVIEW,
                    level=ReasonLevel.REVIEW,
                    evidence={
                        "message": "Vendor identity has not been resolved to a vendor record."
                    },
                ),
                None,
                header_checks,
                case_reasons,
            )
        if invoice_gstin and gstin_result is not None and not gstin_result.is_valid:
            _add_case_check(
                invoice,
                _make_check(
                    "s6.vendor_gstin_valid",
                    CheckOutcome.FAIL,
                    code=ReasonCode.GSTIN_INVALID,
                    level=ReasonLevel.FAIL,
                    actual=invoice_gstin,
                ),
                ReasonCode.GSTIN_INVALID,
                header_checks,
                case_reasons,
            )
        buyer_gstin = _text(_value(header.buyer_gstin))
        if buyer_gstin:
            buyer_validation = validate_gstin(buyer_gstin)
            if not buyer_validation.is_valid:
                _add_case_check(
                    invoice,
                    _make_check(
                        "s6.buyer_gstin_valid",
                        CheckOutcome.FAIL,
                        code=ReasonCode.GSTIN_INVALID,
                        level=ReasonLevel.FAIL,
                        actual=buyer_gstin,
                    ),
                    ReasonCode.GSTIN_INVALID,
                    header_checks,
                    case_reasons,
                )
            elif config.buyer_entities and buyer_validation.normalized not in {
                item.upper().replace(" ", "") for item in config.buyer_entities
            }:
                _add_case_check(
                    invoice,
                    _make_check(
                        "s6.buyer_entity",
                        CheckOutcome.FAIL,
                        code=ReasonCode.GSTIN_INVALID,
                        level=ReasonLevel.FAIL,
                        actual=buyer_validation.normalized,
                        evidence={"configured_entities": list(config.buyer_entities)},
                    ),
                    ReasonCode.GSTIN_INVALID,
                    header_checks,
                    case_reasons,
                )

        for work in sorted(invoice_works, key=lambda item: item.line.line_no):
            amount = _line_amount(work.line)
            work.line_taxable = amount or _ZERO
            quantity = _line_qty(work.line)
            if work.po is None:
                continue
            matched_any = True
            po_header = work.po.document.header
            po_currency = _text(_value(po_header.currency))
            if work.po.document_id not in checked_pairs:
                checked_pairs.add(work.po.document_id)
                if invoice_currency and po_currency and invoice_currency != po_currency:
                    _add_work_check(
                        work,
                        _make_check(
                            "s6.currency",
                            CheckOutcome.FAIL,
                            code=ReasonCode.CURRENCY_DIFF,
                            level=ReasonLevel.FAIL,
                            expected=po_currency,
                            actual=invoice_currency,
                        ),
                        ReasonCode.CURRENCY_DIFF,
                    )
                po_date = _doc_date(work.po.document)
                invoice_date = _doc_date(invoice.document)
                if po_date is not None and invoice_date is not None and invoice_date < po_date:
                    failing = config.date_order_level == "fail"
                    _add_work_check(
                        work,
                        _make_check(
                            "s6.date_order",
                            CheckOutcome.FAIL if failing else CheckOutcome.WARN,
                            code=ReasonCode.DATE_ORDER,
                            level=ReasonLevel.FAIL if failing else ReasonLevel.WARN,
                            expected=po_date.isoformat(),
                            actual=invoice_date.isoformat(),
                        ),
                        ReasonCode.DATE_ORDER,
                    )
                if invoice.vendor_id is not None and work.po.vendor_id != invoice.vendor_id:
                    _add_work_check(
                        work,
                        _make_check(
                            "s6.vendor_match",
                            CheckOutcome.FAIL,
                            code=ReasonCode.VENDOR_MISMATCH,
                            level=ReasonLevel.FAIL,
                            evidence={
                                "invoice_vendor_id": str(invoice.vendor_id),
                                "po_vendor_id": str(work.po.vendor_id),
                            },
                        ),
                        ReasonCode.VENDOR_MISMATCH,
                    )
                elif work.po.vendor_id is not None and invoice_gstin:
                    expected_pan = snapshot.vendor_pan_by_id.get(work.po.vendor_id)
                    actual_pan = (
                        gstin_result.pan if gstin_result and gstin_result.format_valid else None
                    )
                    if expected_pan and actual_pan and expected_pan.upper() != actual_pan:
                        _add_work_check(
                            work,
                            _make_check(
                                "s6.vendor_pan",
                                CheckOutcome.FAIL,
                                code=ReasonCode.VENDOR_MISMATCH,
                                level=ReasonLevel.FAIL,
                                expected=expected_pan.upper(),
                                actual=actual_pan,
                            ),
                            ReasonCode.VENDOR_MISMATCH,
                        )
                po_date = _doc_date(work.po.document)
                po_header = work.po.document.header
                delivery_date = (
                    _value(po_header.delivery_date) if isinstance(po_header, POHeader) else None
                )
                for grn_id in sorted(work.grn_ids, key=str):
                    grn_record = documents[grn_id]
                    grn_date = _doc_date(grn_record.document)
                    if grn_date is not None and po_date is not None and grn_date < po_date:
                        failing = config.date_order_level == "fail"
                        _add_work_check(
                            work,
                            _make_check(
                                "s6.grn_date_order",
                                CheckOutcome.FAIL if failing else CheckOutcome.WARN,
                                code=ReasonCode.DATE_ORDER,
                                level=ReasonLevel.FAIL if failing else ReasonLevel.WARN,
                                expected=po_date.isoformat(),
                                actual=grn_date.isoformat(),
                            ),
                            ReasonCode.DATE_ORDER,
                        )
                    if (
                        grn_date is not None
                        and isinstance(delivery_date, date)
                        and grn_date > delivery_date
                    ):
                        _add_work_check(
                            work,
                            _make_check(
                                "s6.late_delivery",
                                CheckOutcome.INFO,
                                code=ReasonCode.LATE_DELIVERY,
                                level=ReasonLevel.INFO,
                                expected=delivery_date.isoformat(),
                                actual=grn_date.isoformat(),
                            ),
                            ReasonCode.LATE_DELIVERY,
                        )

            due = _payment_due(invoice.document, work.po.document)
            if due is not None:
                due_dates.setdefault(invoice.document_id, set()).add(due)
            line_type_value = _value(work.line.line_type)
            if line_type_value is LineType.CHARGE:
                description = normalize_item_text(
                    _text(_value(work.line.description)) or ""
                ).description
                approved = any(
                    _value(po_line.line_type) is LineType.CHARGE
                    and normalize_item_text(_text(_value(po_line.description)) or "").description
                    == description
                    for po_line in work.po.document.lines
                )
                work.approved_charge = approved
                if approved:
                    expected += amount or _ZERO
                else:
                    _add_work_check(
                        work,
                        _make_check(
                            "s5.extra_charge",
                            CheckOutcome.WARN,
                            code=ReasonCode.EXTRA_CHARGE,
                            level=ReasonLevel.WARN,
                            actual=amount,
                        ),
                        ReasonCode.EXTRA_CHARGE,
                    )
                work.eligible = None
                work.payable_rate = None
                work.payable_taxable = amount or _ZERO if approved else _ZERO
                work.at_risk = (amount or _ZERO) - work.payable_taxable
                if (
                    approved
                    and invoice.document_id not in exact_duplicate_ids
                    and invoice.document_id not in snapshot.rejected_invoice_ids
                ):
                    po_invoice_amounts = po_values.setdefault(work.po.document_id, {})
                    prior_invoice_amount = po_invoice_amounts.get(
                        invoice.document_id, (invoice, _ZERO)
                    )[1]
                    po_invoice_amounts[invoice.document_id] = (
                        invoice,
                        prior_invoice_amount + work.line_taxable,
                    )
            elif work.po_line is not None:
                po_rate = _field_decimal(work.po_line, "rate")
                invoice_rate = _invoice_net_rate(work.line)
                work.po_rate = po_rate
                work.invoice_rate = invoice_rate
                if quantity is not None and po_rate is not None:
                    po_value_at_contract_rate = quantity * po_rate
                    expected += po_value_at_contract_rate
                    if (
                        invoice.document_id not in exact_duplicate_ids
                        and invoice.document_id not in snapshot.rejected_invoice_ids
                    ):
                        po_invoice_amounts = po_values.setdefault(work.po.document_id, {})
                        prior_invoice_amount = po_invoice_amounts.get(
                            invoice.document_id, (invoice, _ZERO)
                        )[1]
                        po_invoice_amounts[work.invoice.document_id] = (
                            invoice,
                            prior_invoice_amount + po_value_at_contract_rate,
                        )
                if po_rate is not None and invoice_rate is not None:
                    rate_rule = _rule(config, "unit_rate", invoice.vendor_id, work.line)
                    variance = invoice_rate - po_rate
                    within = _tolerance_pass(invoice_rate, po_rate, rate_rule)
                    if within:
                        outcome = CheckOutcome.PASS
                        reason = None
                        level = None
                    else:
                        high = variance > _ZERO
                        reason = ReasonCode.RATE_HIGH if high else ReasonCode.RATE_LOW
                        outcome = CheckOutcome.FAIL if high else CheckOutcome.WARN
                        level = ReasonLevel.FAIL if high else ReasonLevel.WARN
                    _add_work_check(
                        work,
                        _make_check(
                            "s5.unit_rate",
                            outcome,
                            code=reason,
                            level=level,
                            expected=po_rate,
                            actual=invoice_rate,
                            variance=variance,
                            tolerance=rate_rule,
                        ),
                        reason,
                    )
                hsn_invoice = _text(_value(work.line.hsn_sac))
                hsn_po = _text(_value(work.po_line.hsn_sac))
                if (
                    hsn_invoice
                    and hsn_po
                    and re.sub(r"\D", "", hsn_invoice) != re.sub(r"\D", "", hsn_po)
                ):
                    _add_work_check(
                        work,
                        _make_check(
                            "s6.hsn",
                            CheckOutcome.WARN,
                            code=ReasonCode.HSN_DIFF,
                            level=ReasonLevel.WARN,
                            expected=hsn_po,
                            actual=hsn_invoice,
                        ),
                        ReasonCode.HSN_DIFF,
                    )
                invoice_uom = _text(_value(work.line.uom))
                po_uom = _text(_value(work.po_line.uom))
                if invoice_uom and po_uom:
                    normalized_invoice = normalize_uom(invoice_uom, config.item_unit_aliases)
                    normalized_po = normalize_uom(po_uom, config.item_unit_aliases)
                    unit_mismatch = (
                        normalized_invoice.canonical != normalized_po.canonical
                        if normalized_invoice.canonical and normalized_po.canonical
                        else invoice_uom.strip().casefold() != po_uom.strip().casefold()
                    )
                    if unit_mismatch:
                        _add_work_check(
                            work,
                            _make_check(
                                "s6.uom",
                                CheckOutcome.FAIL,
                                code=ReasonCode.UOM_MISMATCH,
                                level=ReasonLevel.FAIL,
                                expected=po_uom,
                                actual=invoice_uom,
                            ),
                            ReasonCode.UOM_MISMATCH,
                        )
                invoice_tax_rate = _field_decimal(work.line, "tax_rate")
                po_tax_rate = _field_decimal(work.po_line, "tax_rate")
                if invoice_tax_rate is not None:
                    if invoice_tax_rate not in config.tax_rates:
                        _add_work_check(
                            work,
                            _make_check(
                                "s6.tax_slab",
                                CheckOutcome.WARN,
                                code=ReasonCode.TAX_RATE_DIFF,
                                level=ReasonLevel.WARN,
                                actual=invoice_tax_rate,
                                evidence={
                                    "allowed_rates": [str(rate) for rate in config.tax_rates]
                                },
                            ),
                            ReasonCode.TAX_RATE_DIFF,
                        )
                    if po_tax_rate is not None and invoice_tax_rate != po_tax_rate:
                        _add_work_check(
                            work,
                            _make_check(
                                "s6.tax_rate_diff",
                                CheckOutcome.WARN,
                                code=ReasonCode.TAX_RATE_DIFF,
                                level=ReasonLevel.WARN,
                                expected=po_tax_rate,
                                actual=invoice_tax_rate,
                            ),
                            ReasonCode.TAX_RATE_DIFF,
                        )
                if work.po_line is not None and work.alignment is not None:
                    work.po_rate = po_rate
                if work.eligible is not None and invoice_rate is not None and po_rate is not None:
                    high_rate = any(item.code is ReasonCode.RATE_HIGH for item in work.checks)
                    if config.payable_rate_mode == "hold" and high_rate:
                        payable_rate = _ZERO
                    elif config.payable_rate_mode == "hold":
                        payable_rate = invoice_rate
                    else:
                        payable_rate = min(invoice_rate, po_rate)
                    work.payable_rate = payable_rate
                    work.payable_taxable = work.eligible * payable_rate
                    work.at_risk = work.line_taxable - work.payable_taxable
                else:
                    work.payable_taxable = _ZERO
                    work.at_risk = work.line_taxable
            rate = _tax_rate_for_line(work)
            if rate is None and _taxable(invoice.document) not in (None, _ZERO):
                header_tax = sum(
                    (
                        _decimal(_value(getattr(header, name))) or _ZERO
                        for name in ("cgst", "sgst", "igst")
                    ),
                    _ZERO,
                )
                rate = header_tax / (_taxable(invoice.document) or _ONE) * _HUNDRED
            if (
                invoice.document_id in exact_duplicate_ids
                or invoice.document_id in snapshot.rejected_invoice_ids
            ):
                work.payable_rate = None
                work.payable_taxable = _ZERO
                work.at_risk = _ZERO
            payable_by_invoice[invoice.document_id] = (
                payable_by_invoice.get(invoice.document_id, _ZERO) + work.payable_taxable
            )
            risk_by_invoice[invoice.document_id] = (
                risk_by_invoice.get(invoice.document_id, _ZERO) + work.at_risk
            )
            if rate is not None:
                risk_gst_by_invoice[invoice.document_id] = (
                    risk_gst_by_invoice.get(invoice.document_id, _ZERO)
                    + work.at_risk * rate / _HUNDRED
                )

        expected_by_invoice[invoice.document_id] = expected if matched_any else _ZERO
        arithmetic_rule = _rule(config, "arithmetic", invoice.vendor_id, None)
        line_total = _ZERO
        line_total_available = True
        for line in invoice.document.lines:
            quantity = _line_qty(line)
            rate = _field_decimal(line, "rate")
            amount = _line_amount(line)
            discount = _field_decimal(line, "discount") or _ZERO
            if amount is not None:
                line_total += amount
            else:
                line_total_available = False
            if quantity is not None and rate is not None and amount is not None:
                _check_math(
                    header_checks[invoice.document_id],
                    case_reasons[invoice.document_id],
                    "s5.line_math",
                    ReasonCode.MATH_ERROR,
                    amount,
                    quantity * rate - discount,
                    arithmetic_rule,
                )
        taxable = _taxable(invoice.document)
        if taxable is not None and line_total_available and invoice.document.lines:
            _check_math(
                header_checks[invoice.document_id],
                case_reasons[invoice.document_id],
                "s5.taxable_sum",
                ReasonCode.MATH_ERROR,
                taxable,
                line_total,
                arithmetic_rule,
            )
        total = _grand_total(invoice.document)
        if taxable is not None and total is not None:
            declared_tax = sum(
                (
                    _decimal(_value(getattr(header_line, name))) or _ZERO
                    for header_line in (header,)
                    for name in ("cgst", "sgst", "igst", "round_off")
                ),
                _ZERO,
            )
            _check_math(
                header_checks[invoice.document_id],
                case_reasons[invoice.document_id],
                "s5.grand_total",
                ReasonCode.MATH_ERROR,
                total,
                taxable + declared_tax,
                arithmetic_rule,
            )
        if expected:
            tolerance = _rule(config, "total_vs_expected", invoice.vendor_id, None)
            if taxable is not None:
                _check_math(
                    header_checks[invoice.document_id],
                    case_reasons[invoice.document_id],
                    "s5.total_vs_expected",
                    ReasonCode.TOTAL_MISMATCH,
                    taxable,
                    expected,
                    tolerance,
                )
        supplier_state = _gst_state(invoice_gstin)
        buyer_state = _gst_state(buyer_gstin)
        if buyer_state is None:
            place_of_supply = _text(_value(header.place_of_supply))
            buyer_state = (
                place_of_supply[:2] if place_of_supply and place_of_supply[:2].isdigit() else None
            )
        if supplier_state and buyer_state:
            has_igst = (_decimal(_value(header.igst)) or _ZERO) > _ZERO
            has_split_tax = (_decimal(_value(header.cgst)) or _ZERO) > _ZERO or (
                _decimal(_value(header.sgst)) or _ZERO
            ) > _ZERO
            mismatch = (supplier_state == buyer_state and has_igst) or (
                supplier_state != buyer_state and has_split_tax
            )
            if mismatch:
                _add_case_check(
                    invoice,
                    _make_check(
                        "s6.tax_type",
                        CheckOutcome.WARN,
                        code=ReasonCode.TAX_TYPE_MISMATCH,
                        level=ReasonLevel.WARN,
                        evidence={"supplier_state": supplier_state, "buyer_state": buyer_state},
                    ),
                    ReasonCode.TAX_TYPE_MISMATCH,
                    header_checks,
                    case_reasons,
                )
        tax_bases: list[Decimal] = []
        for line in invoice.document.lines:
            line_amount = _line_amount(line)
            tax_rate = _field_decimal(line, "tax_rate")
            if line_amount is not None and tax_rate is not None:
                tax_bases.append(line_amount * tax_rate / _HUNDRED)
        if tax_bases:
            expected_tax = sum(tax_bases, _ZERO)
            actual_tax = sum(
                (
                    _decimal(_value(getattr(header, name))) or _ZERO
                    for name in ("cgst", "sgst", "igst")
                ),
                _ZERO,
            )
            _check_math(
                header_checks[invoice.document_id],
                case_reasons[invoice.document_id],
                "s6.computed_tax",
                ReasonCode.MATH_ERROR,
                actual_tax,
                expected_tax,
                arithmetic_rule,
            )

    for po_id, invoice_amounts in po_values.items():
        po = documents[po_id]
        po_total = _taxable(po.document)
        if po_total is None:
            continue
        billed_value = _ZERO
        ordered_amounts = sorted(
            invoice_amounts.values(),
            key=lambda item: (
                _doc_date(item[0].document) or date.max,
                canonicalize_document_number(_doc_no(item[0].document)),
                item[0].natural_key or str(item[0].document_id),
                str(item[0].document_id),
            ),
        )
        for invoice, line_amount in ordered_amounts:
            billed_value += line_amount
            tolerance = _rule(config, "cum_value_vs_po", invoice.vendor_id, None)
            if billed_value > po_total and not _tolerance_pass(billed_value, po_total, tolerance):
                _add_case_check(
                    invoice,
                    _make_check(
                        "s5.cumulative_po_value",
                        CheckOutcome.FAIL,
                        code=ReasonCode.PO_VALUE_EXCEEDED,
                        level=ReasonLevel.FAIL,
                        expected=po_total,
                        actual=billed_value,
                        variance=billed_value - po_total,
                        tolerance=tolerance,
                    ),
                    ReasonCode.PO_VALUE_EXCEEDED,
                    header_checks,
                    case_reasons,
                )

    output: dict[UUID, _InvoiceFinance] = {}
    for invoice in invoices:
        due_set = due_dates.get(invoice.document_id, set())
        output[invoice.document_id] = _InvoiceFinance(
            expected=expected_by_invoice.get(invoice.document_id),
            payable=payable_by_invoice.get(invoice.document_id, _ZERO),
            at_risk=risk_by_invoice.get(invoice.document_id, _ZERO),
            at_risk_gst=risk_gst_by_invoice.get(invoice.document_id, _ZERO),
            payment_due_date=next(iter(due_set)) if len(due_set) == 1 else None,
        )
    return output


def evaluate(
    snapshot: ReconciliationSnapshot,
    config: ReconciliationConfig,
    run_date: date,
    *,
    engine_version: str = ENGINE_VERSION,
) -> tuple[CaseResult, ...]:
    """Evaluate invoice cases without I/O, clocks, mutation of inputs, or randomness."""

    fingerprint = rules_hash(config)
    documents = {item.document_id: item for item in snapshot.documents}
    invoices = tuple(
        item for item in snapshot.documents if item.document.header.doc_type is DocType.INVOICE
    )
    po_documents = {
        item.document_id: item
        for item in snapshot.documents
        if item.document.header.doc_type is DocType.PO
    }
    active_links = _active_links(snapshot)
    exact_duplicates, suspected_duplicates = _duplicate_map(invoices, snapshot, config)
    grn_accepted, grn_rejected, grn_ids_by_line, grn_fallback = _build_grn_ledger(
        snapshot, documents, po_documents, active_links, config
    )
    earliest_related: dict[tuple[UUID, int], date] = {}
    for link in active_links:
        if documents[link.to_document_id].document.header.doc_type is not DocType.PO:
            continue
        related = documents[link.from_document_id]
        related_date = _doc_date(related.document)
        if related_date is None:
            continue
        po = po_documents.get(link.to_document_id)
        if po is None:
            continue
        selected_lines = tuple(
            line
            for line in related.document.lines
            if link.level is LinkLevel.HEADER or line.line_no == link.line_no
        )
        for match in align_lines(selected_lines, po.document.lines, config.alignment):
            if match.po_line_no is None or match.state not in {
                AlignmentState.ALIGNED,
                AlignmentState.UNSURE,
            }:
                continue
            key = (link.to_document_id, match.po_line_no)
            current = earliest_related.get(key)
            if current is None or related_date < current:
                earliest_related[key] = related_date

    pool_start = min(earliest_related.values(), default=None)
    works: list[_LineWork] = []
    case_header_checks: dict[UUID, list[CheckResult]] = {item.document_id: [] for item in invoices}
    case_reasons: dict[UUID, list[ReasonCode]] = {item.document_id: [] for item in invoices}
    gate_confidence = {item.document_id: _gate_confidence(item.document) for item in invoices}
    unverified_ai = {item.document_id: _unverified_ai_values(item.document) for item in invoices}
    for invoice in invoices:
        confidence = gate_confidence[invoice.document_id]
        # An unreadable scan is reported as such; its missing PO link is a symptom.
        unreadable = ReasonCode.NO_TEXT_LAYER in invoice.document.reason_codes
        # An invoice split out of a multi-document file waits for the split to be accepted.
        held = split_unconfirmed(invoice.document)
        if unreadable or held or confidence is None or confidence < config.extraction_gate:
            gate_code = ReasonCode.NO_TEXT_LAYER if unreadable else ReasonCode.LOW_CONFIDENCE_DATA
            case_header_checks[invoice.document_id].append(
                _make_check(
                    "s1.extraction_gate",
                    CheckOutcome.REVIEW,
                    code=gate_code,
                    level=ReasonLevel.REVIEW,
                    expected=config.extraction_gate,
                    actual=confidence,
                    evidence={
                        "message": (
                            "The scan has no readable text; enter or re-upload the invoice."
                            if unreadable
                            else "The file holds several documents; accept or undo the page "
                            "split first."
                            if held
                            else "Invoice critical fields are missing or below confidence gate."
                        )
                    },
                )
            )
            case_reasons[invoice.document_id].append(gate_code)
        if unverified_ai[invoice.document_id]:
            # A value read by AI counts only after a person verifies it (PD-2b).
            case_header_checks[invoice.document_id].append(
                _make_check(
                    "s1.ai_values_verified",
                    CheckOutcome.REVIEW,
                    code=ReasonCode.AI_VALUE_UNVERIFIED,
                    level=ReasonLevel.REVIEW,
                    expected=Decimal("0"),
                    actual=Decimal(len(unverified_ai[invoice.document_id])),
                    evidence={
                        "message": "Values read by AI count only after a person verifies them.",
                        "fields": list(unverified_ai[invoice.document_id]),
                    },
                )
            )
            case_reasons[invoice.document_id].append(ReasonCode.AI_VALUE_UNVERIFIED)
        duplicate_id = exact_duplicates.get(invoice.document_id)
        if duplicate_id is not None:
            case_header_checks[invoice.document_id].append(
                _make_check(
                    "s1.exact_duplicate",
                    CheckOutcome.BLOCK,
                    code=ReasonCode.DUP_INVOICE,
                    level=ReasonLevel.BLOCK,
                    actual=_doc_no(invoice.document),
                    evidence={"duplicate_of_id": str(duplicate_id)},
                )
            )
            case_reasons[invoice.document_id].append(ReasonCode.DUP_INVOICE)
        elif invoice.document_id in suspected_duplicates:
            duplicate_id = suspected_duplicates[invoice.document_id]
            case_header_checks[invoice.document_id].append(
                _make_check(
                    "s1.suspected_duplicate",
                    CheckOutcome.REVIEW,
                    code=ReasonCode.DUP_SUSPECTED,
                    level=ReasonLevel.REVIEW,
                    actual=_doc_no(invoice.document),
                    evidence={"possible_duplicate_of_id": str(duplicate_id)},
                )
            )
            case_reasons[invoice.document_id].append(ReasonCode.DUP_SUSPECTED)

        invoice_links = tuple(
            link
            for link in active_links
            if link.from_document_id == invoice.document_id
            and documents[link.to_document_id].document.header.doc_type is DocType.PO
        )
        if not invoice_links and not unreadable:
            case_header_checks[invoice.document_id].append(
                _make_check(
                    "s2.po_link",
                    CheckOutcome.BLOCK,
                    code=ReasonCode.NO_PO_LINK,
                    level=ReasonLevel.BLOCK,
                )
            )
            case_reasons[invoice.document_id].append(ReasonCode.NO_PO_LINK)
        line_choices: dict[int, tuple[ReconciliationLink, tuple[ReconciliationLink, ...]]] = {}
        lines_by_po: dict[UUID, list[DocLine]] = {}
        links_by_po: dict[UUID, list[ReconciliationLink]] = {}
        for line in sorted(invoice.document.lines, key=lambda item: item.line_no):
            line_links = _line_links_for_invoice(invoice.document_id, line.line_no, invoice_links)
            if not line_links:
                continue
            ranked_links = tuple(
                sorted(
                    line_links,
                    key=lambda item: (
                        -item.confidence,
                        str(item.to_document_id),
                        str(item.link_id),
                    ),
                )
            )
            selected_link = ranked_links[0]
            line_choices[line.line_no] = (selected_link, ranked_links)
            lines_by_po.setdefault(selected_link.to_document_id, []).append(line)
            links_by_po.setdefault(selected_link.to_document_id, []).append(selected_link)

        alignment_by_line: dict[tuple[UUID, int], AlignmentResult] = {}
        for po_id, po_invoice_lines in sorted(lines_by_po.items(), key=lambda item: str(item[0])):
            po_record = po_documents.get(po_id)
            if po_record is None:
                continue
            chosen_links = links_by_po[po_id]
            explicit_link = all(link.method is not LinkMethod.CONTENT for link in chosen_links)
            for alignment in align_lines(
                po_invoice_lines,
                po_record.document.lines,
                config.alignment,
                explicit_link=explicit_link,
            ):
                alignment_by_line[(po_id, alignment.invoice_line_no)] = alignment

        checked_link_ids: set[UUID] = set()
        checked_grn_link_ids: set[UUID] = set()
        for line in sorted(invoice.document.lines, key=lambda item: item.line_no):
            choice = line_choices.get(line.line_no)
            if choice is None:
                works.append(_LineWork(invoice, line, None, None, None, None))
                continue
            selected_link, ranked_links = choice
            if len({item.to_document_id for item in ranked_links}) > 1:
                case_header_checks[invoice.document_id].append(
                    _make_check(
                        "s2.multiple_pos_line",
                        CheckOutcome.REVIEW,
                        code=ReasonCode.NO_PO_LINK,
                        level=ReasonLevel.REVIEW,
                        evidence={
                            "line_no": line.line_no,
                            "candidate_po_ids": [str(item.to_document_id) for item in ranked_links],
                        },
                    )
                )
                case_reasons[invoice.document_id].append(ReasonCode.NO_PO_LINK)
            po_record = po_documents.get(selected_link.to_document_id)
            if po_record is None:
                works.append(_LineWork(invoice, line, None, None, None, None))
                continue
            if selected_link.link_id not in checked_link_ids and _link_needs_confirming(
                selected_link, config
            ):
                checked_link_ids.add(selected_link.link_id)
                case_header_checks[invoice.document_id].append(
                    _make_check(
                        "s2.link_confidence",
                        CheckOutcome.REVIEW,
                        code=ReasonCode.LINK_INFERRED,
                        level=ReasonLevel.REVIEW,
                        expected=config.link_auto_accept,
                        actual=selected_link.confidence,
                        evidence={"method": selected_link.method.value},
                    )
                )
            for grn_link in active_links:
                grn_record = documents[grn_link.from_document_id]
                if (
                    grn_record.document.header.doc_type is not DocType.GRN
                    or grn_link.to_document_id != selected_link.to_document_id
                    or grn_link.link_id in checked_grn_link_ids
                    or not _link_needs_confirming(grn_link, config)
                ):
                    continue
                checked_grn_link_ids.add(grn_link.link_id)
                case_header_checks[invoice.document_id].append(
                    _make_check(
                        "s2.link_confidence",
                        CheckOutcome.REVIEW,
                        code=ReasonCode.LINK_INFERRED,
                        level=ReasonLevel.REVIEW,
                        expected=config.link_auto_accept,
                        actual=grn_link.confidence,
                        evidence={
                            "document_type": "grn",
                            "grn_document_id": str(grn_link.from_document_id),
                            "method": grn_link.method.value,
                        },
                    )
                )
                case_reasons[invoice.document_id].append(ReasonCode.LINK_INFERRED)
            po_header = po_record.document.header
            po_status = _value(po_header.status) if isinstance(po_header, POHeader) else None
            prechecks: list[CheckResult] = []
            pre_reasons: list[ReasonCode] = []
            if po_status in {POStatus.CLOSED, POStatus.CANCELLED}:
                prechecks.append(
                    _make_check(
                        "s1.po_open",
                        CheckOutcome.FAIL,
                        code=ReasonCode.PO_NOT_OPEN,
                        level=ReasonLevel.BLOCK,
                        actual=po_status.value,
                    )
                )
                pre_reasons.append(ReasonCode.PO_NOT_OPEN)
            elif po_status is None:
                # PRD: PO_NOT_OPEN fails only a closed or cancelled PO. Most ERP and
                # Tally PO exports carry no status, so absence is not evidence of a
                # closed PO; the assumption stays visible on the case (D19).
                prechecks.append(
                    _make_check(
                        "s1.po_open",
                        CheckOutcome.PASS,
                        expected=POStatus.OPEN.value,
                        evidence={
                            "message": "The PO file has no status; the PO is treated as open."
                        },
                    )
                )
            line_alignment = alignment_by_line.get((po_record.document_id, line.line_no))
            po_line = next(
                (
                    candidate
                    for candidate in po_record.document.lines
                    if line_alignment is not None and candidate.line_no == line_alignment.po_line_no
                ),
                None,
            )
            work = _LineWork(invoice, line, selected_link, po_record, po_line, line_alignment)
            work.checks.extend(prechecks)
            work.reasons.extend(pre_reasons)
            is_charge = _value(line.line_type) is LineType.CHARGE
            if not is_charge and (
                line_alignment is None or line_alignment.state is AlignmentState.NOT_ON_PO
            ):
                work.checks.append(
                    _make_check(
                        "s3.item_alignment",
                        CheckOutcome.FAIL,
                        code=ReasonCode.ITEM_NOT_ON_PO,
                        level=ReasonLevel.FAIL,
                        evidence={
                            "line_no": line.line_no,
                            "po_document_id": str(po_record.document_id),
                        },
                    )
                )
                work.reasons.append(ReasonCode.ITEM_NOT_ON_PO)
            works.append(work)

    work_by_ledger: dict[tuple[UUID, int], list[_LineWork]] = {}
    for work in works:
        if work.po is None or work.po_line is None:
            continue
        work_by_ledger.setdefault((work.po.document_id, work.po_line.line_no), []).append(work)

    opening_keys = {(item.po_document_id, item.po_line_no) for item in snapshot.opening_balances}
    opening_billed_by_line: dict[tuple[UUID, int], Decimal] = {}
    for item in snapshot.opening_balances:
        opening_billed_by_line[(item.po_document_id, item.po_line_no)] = (
            opening_billed_by_line.get((item.po_document_id, item.po_line_no), _ZERO)
            + item.billed_qty
        )
    for ledger_key, line_works in work_by_ledger.items():
        po_id, po_line_no = ledger_key
        po_record = po_documents[po_id]
        po_line = next(line for line in po_record.document.lines if line.line_no == po_line_no)
        ordered_qty = _line_qty(po_line)
        billed = opening_billed_by_line.get(ledger_key, _ZERO)
        accepted_qty = grn_accepted.get(ledger_key, _ZERO)
        rejected_qty = grn_rejected.get(ledger_key, _ZERO)
        po_tolerance = _rule(config, "cum_qty_vs_po", po_record.vendor_id, po_line)
        po_cap = (
            ordered_qty + _tolerance_allowance(ordered_qty, po_tolerance)
            if ordered_qty is not None
            else None
        )
        if po_cap is not None and accepted_qty + rejected_qty > po_cap:
            for work in line_works:
                _add_work_check(
                    work,
                    _make_check(
                        "s4.grn_over_po",
                        CheckOutcome.WARN,
                        code=ReasonCode.GRN_OVER_PO,
                        level=ReasonLevel.WARN,
                        expected=po_cap,
                        actual=accepted_qty + rejected_qty,
                    ),
                    ReasonCode.GRN_OVER_PO,
                )
        related_date = earliest_related.get(ledger_key)
        po_date = _doc_date(po_record.document)
        # PRD: warn when earlier months are not in the pool, i.e. the PO predates
        # the first month of uploaded receipts and invoices. A PO raised earlier
        # in the same month as its first receipt is the normal case.
        if (
            related_date is not None
            and po_date is not None
            and pool_start is not None
            and po_date < pool_start.replace(day=1)
            and ledger_key not in opening_keys
        ):
            for work in line_works:
                work.checks.append(
                    _make_check(
                        "s4.ledger_completeness",
                        CheckOutcome.WARN,
                        level=ReasonLevel.WARN,
                        evidence={
                            "message": (
                                "Earlier ledger activity may be missing; import opening balances."
                            ),
                            "po_date": po_date.isoformat(),
                            "earliest_in_pool_date": related_date.isoformat(),
                        },
                    )
                )
        for work in sorted(
            line_works,
            key=lambda item: (
                _doc_date(item.invoice.document) or date.max,
                canonicalize_document_number(_doc_no(item.invoice.document)),
                item.invoice.natural_key or str(item.invoice.document_id),
                item.line.line_no,
            ),
        ):
            work.grn_ids.update(grn_ids_by_line.get(ledger_key, set()))
            if work.alignment is not None:
                if work.alignment.state is AlignmentState.UNSURE:
                    work.checks.append(
                        _make_check(
                            "s3.item_alignment",
                            CheckOutcome.REVIEW,
                            code=ReasonCode.ITEM_UNSURE,
                            level=ReasonLevel.REVIEW,
                            actual=work.alignment.score,
                        )
                    )
                    work.reasons.append(ReasonCode.ITEM_UNSURE)
                elif work.alignment.state is AlignmentState.NOT_ON_PO:
                    work.checks.append(
                        _make_check(
                            "s3.item_alignment",
                            CheckOutcome.FAIL,
                            code=ReasonCode.ITEM_NOT_ON_PO,
                            level=ReasonLevel.FAIL,
                            actual=work.alignment.score,
                        )
                    )
                    work.reasons.append(ReasonCode.ITEM_NOT_ON_PO)
            quantity = _line_qty(work.line)
            if (
                work.invoice.document_id in exact_duplicates
                or work.invoice.document_id in snapshot.rejected_invoice_ids
                or quantity is None
                or ordered_qty is None
                or po_cap is None
            ):
                continue
            before = billed
            after = before + quantity
            # TR-REC-7: quantity received above the PO cap is not billable.
            grn_cum = min(accepted_qty, po_cap)
            qty_tolerance = _rule(config, "cum_qty_vs_po", work.invoice.vendor_id, work.line)
            over_po = after > po_cap
            eligible = max(_ZERO, min(quantity, po_cap - before, grn_cum - before))
            grn_tolerance = _rule(config, "qty_vs_grn", work.invoice.vendor_id, work.line)
            shortfall = after - grn_cum
            work.po_cap = po_cap
            work.billed_before = before
            work.billed_qty = quantity
            work.grn_accepted = grn_cum
            work.grn_rejected = rejected_qty
            work.eligible = eligible
            work.balance_after = max(_ZERO, ordered_qty - after)
            work.shortfall = max(_ZERO, shortfall)
            if over_po:
                work.checks.append(
                    _make_check(
                        "s4.cumulative_qty_vs_po",
                        CheckOutcome.FAIL,
                        code=ReasonCode.QTY_OVER_PO,
                        level=ReasonLevel.FAIL,
                        expected=po_cap,
                        actual=after,
                        variance=after - ordered_qty,
                        tolerance=qty_tolerance,
                    )
                )
                work.reasons.append(ReasonCode.QTY_OVER_PO)
            if shortfall > _ZERO and not _tolerance_pass(after, grn_cum, grn_tolerance):
                rejected_explains_shortfall = rejected_qty > _ZERO and shortfall <= rejected_qty
                if over_po or rejected_explains_shortfall:
                    work.checks.append(
                        _make_check(
                            "s4.cumulative_qty_vs_grn",
                            CheckOutcome.FAIL,
                            code=ReasonCode.QTY_OVER_GRN,
                            level=ReasonLevel.FAIL,
                            expected=grn_cum,
                            actual=after,
                            variance=shortfall,
                            evidence={"rejected_qty": str(rejected_qty), "over_po": over_po},
                        )
                    )
                    work.reasons.append(ReasonCode.QTY_OVER_GRN)
                else:
                    work.checks.append(
                        _make_check(
                            "s4.grn_pending",
                            CheckOutcome.PENDING,
                            code=ReasonCode.GRN_PENDING,
                            level=ReasonLevel.PENDING,
                            expected=after,
                            actual=grn_cum,
                            variance=shortfall,
                        )
                    )
                    work.reasons.append(ReasonCode.GRN_PENDING)
            if grn_fallback.get(ledger_key):
                work.checks.append(
                    _make_check(
                        "s4.grn_quantity_fallback",
                        CheckOutcome.WARN,
                        level=ReasonLevel.WARN,
                        evidence={
                            "message": (
                                "GRN quantity used received_qty because accepted_qty was absent."
                            )
                        },
                    )
                )
            billed = after

    finance = _apply_financial_checks(
        snapshot,
        config,
        invoices,
        documents,
        works,
        case_header_checks,
        case_reasons,
    )
    result_by_invoice: list[CaseResult] = []
    for invoice in sorted(
        invoices,
        key=lambda item: (
            _doc_date(item.document) or date.max,
            canonicalize_document_number(_doc_no(item.document)),
            item.natural_key or str(item.document_id),
            str(item.document_id),
        ),
    ):
        duplicate_id = exact_duplicates.get(invoice.document_id)
        is_rejected = invoice.document_id in snapshot.rejected_invoice_ids
        invoice_works = [work for work in works if work.invoice.document_id == invoice.document_id]
        header_checks = case_header_checks[invoice.document_id]
        reason_codes = list(case_reasons[invoice.document_id])
        confidence = gate_confidence[invoice.document_id]
        line_results: list[LineResult] = []
        for work in sorted(invoice_works, key=lambda item: item.line.line_no):
            reasons = list(work.reasons)
            checks = list(work.checks)
            if work.po is None:
                line_status = CaseStatus.UNLINKED
                reasons.append(ReasonCode.NO_PO_LINK)
                alignment_state = AlignmentState.UNLINKED
            else:
                alignment_state = (
                    work.alignment.state if work.alignment else AlignmentState.NOT_ON_PO
                )
                low_confidence = (
                    confidence is None
                    or confidence < config.extraction_gate
                    or bool(unverified_ai[invoice.document_id])
                    or work.link is None
                    or (
                        work.link.state is not LinkState.CONFIRMED
                        and work.link.confidence < config.link_auto_accept
                    )
                )
                line_status = _line_status(tuple(checks), low_confidence)
            reason_codes.extend(reasons)
            line_type_value = _value(work.line.line_type)
            if not isinstance(line_type_value, LineType):
                line_type_value = LineType.GOODS
            line_results.append(
                LineResult(
                    engine_version=engine_version,
                    rules_hash=fingerprint,
                    line_no=work.line.line_no,
                    line_type=line_type_value,
                    status=line_status,
                    po_document_id=work.po.document_id if work.po else None,
                    po_line_no=work.po_line.line_no if work.po_line else None,
                    alignment_score=work.alignment.score if work.alignment else None,
                    alignment_state=alignment_state,
                    grn_document_ids=tuple(sorted(work.grn_ids, key=str)),
                    uom=_text(_value(work.line.uom)),
                    po_qty=_line_qty(work.po_line) if work.po_line else None,
                    po_cap=work.po_cap,
                    billed_before=work.billed_before,
                    billed_qty=work.billed_qty,
                    grn_accepted_cum=work.grn_accepted,
                    grn_rejected_cum=work.grn_rejected,
                    eligible_qty=work.eligible,
                    balance_after=work.balance_after,
                    shortfall_qty=work.shortfall,
                    po_rate=work.po_rate,
                    invoice_net_rate=work.invoice_rate,
                    payable_rate=work.payable_rate,
                    line_taxable=work.line_taxable,
                    payable_taxable=work.payable_taxable,
                    at_risk=work.at_risk,
                    checks=tuple(checks),
                    reason_codes=_unique_codes(reasons),
                )
            )
        if duplicate_id is not None:
            status = CaseStatus.DUPLICATE
        elif ReasonCode.NO_TEXT_LAYER in invoice.document.reason_codes:
            status = CaseStatus.NEEDS_REVIEW
        elif not any(link.from_document_id == invoice.document_id for link in active_links) or any(
            line.po_document_id is None for line in line_results
        ):
            status = CaseStatus.UNLINKED
        elif any(check.result is CheckOutcome.REVIEW for check in header_checks) or any(
            line.status is CaseStatus.NEEDS_REVIEW for line in line_results
        ):
            status = CaseStatus.NEEDS_REVIEW
        elif any(
            check.result is CheckOutcome.FAIL or check.result is CheckOutcome.BLOCK
            for check in (
                *header_checks,
                *(check for line in line_results for check in line.checks),
            )
        ):
            status = CaseStatus.EXCEPTION
        elif any(
            check.result is CheckOutcome.PENDING
            for check in (
                *header_checks,
                *(check for line in line_results for check in line.checks),
            )
        ):
            status = CaseStatus.PENDING
        elif any(
            check.result in {CheckOutcome.WARN, CheckOutcome.INFO}
            for check in (
                *header_checks,
                *(check for line in line_results for check in line.checks),
            )
        ):
            status = CaseStatus.MATCHED_FLAGS
        else:
            status = CaseStatus.MATCHED
        if is_rejected and status not in {CaseStatus.DUPLICATE, CaseStatus.UNLINKED}:
            status = CaseStatus.NEEDS_REVIEW

        linked_invoice_pos = tuple(
            link
            for link in active_links
            if link.from_document_id == invoice.document_id
            and documents[link.to_document_id].document.header.doc_type is DocType.PO
        )
        po_ids = sorted({link.to_document_id for link in linked_invoice_pos}, key=str)
        grn_ids_by_po: dict[UUID, tuple[UUID, ...]] = {}
        for po_id in po_ids:
            grn_ids_by_po[po_id] = tuple(
                sorted(
                    {
                        link.from_document_id
                        for link in active_links
                        if link.to_document_id == po_id
                        and documents[link.from_document_id].document.header.doc_type is DocType.GRN
                    },
                    key=str,
                )
            )
        grn_ids = sorted(
            {grn_id for values in grn_ids_by_po.values() for grn_id in values}, key=str
        )
        summaries: list[LinkSummary] = []
        for link in linked_invoice_pos:
            po = documents[link.to_document_id]
            summaries.append(
                LinkSummary(
                    link_id=link.link_id,
                    po_document_id=po.document_id,
                    po_no=_doc_no(po.document),
                    method=link.method,
                    confidence=link.confidence,
                    state=link.state,
                    grn_nos=tuple(
                        sorted(
                            _doc_no(documents[grn_id].document)
                            for grn_id in grn_ids_by_po.get(po.document_id, ())
                        )
                    ),
                )
            )
        invoice_taxable = _taxable(invoice.document)
        invoice_total = _grand_total(invoice.document)
        pending_days = None
        aged = False
        if any(code is ReasonCode.GRN_PENDING for code in reason_codes):
            invoice_date = _doc_date(invoice.document)
            if invoice_date is not None:
                pending_days = max(0, (run_date - invoice_date).days)
                aged = pending_days >= config.pending_ageing_days
                if aged and status is not CaseStatus.EXCEPTION:
                    status = CaseStatus.EXCEPTION
        invoice_finance = finance[invoice.document_id]
        variance = (
            invoice_taxable - invoice_finance.expected
            if invoice_taxable is not None and invoice_finance.expected is not None
            else None
        )
        variance_pct = (
            abs(variance) / abs(invoice_finance.expected) * _HUNDRED
            if variance is not None and invoice_finance.expected not in (None, _ZERO)
            else None
        )
        all_checks = tuple(
            (*header_checks, *(check for line in line_results for check in line.checks))
        )
        blocked = status in {CaseStatus.DUPLICATE, CaseStatus.UNLINKED}
        result_by_invoice.append(
            CaseResult(
                engine_version=engine_version,
                rules_hash=fingerprint,
                invoice_id=invoice.document_id,
                invoice_version_id=invoice.version_id,
                status=status,
                aged=aged,
                pending_days=pending_days,
                duplicate_of_id=duplicate_id,
                invoice_taxable=invoice_taxable,
                invoice_total=invoice_total,
                expected_taxable=invoice_finance.expected,
                variance=variance,
                variance_pct=variance_pct,
                payable_taxable=invoice_finance.payable,
                # A blocked invoice (duplicate or unlinked) carries its value as blocked,
                # never as at risk (schema check recon_case_atrisk_ck; D95).
                at_risk=_ZERO if blocked else invoice_finance.at_risk,
                at_risk_gst=_ZERO if blocked else invoice_finance.at_risk_gst,
                blocked_value=(invoice_taxable or _ZERO) if blocked else _ZERO,
                po_nos=tuple(sorted({_doc_no(documents[item].document) for item in po_ids})),
                grn_nos=tuple(sorted({_doc_no(documents[item].document) for item in grn_ids})),
                payment_due_date=invoice_finance.payment_due_date,
                link_summary=tuple(
                    sorted(summaries, key=lambda item: (item.po_no, str(item.link_id)))
                ),
                reason_codes=_unique_codes(reason_codes),
                checks=all_checks,
                lines=tuple(line_results),
            )
        )
    return tuple(result_by_invoice)


def partition_components(
    snapshot: ReconciliationSnapshot,
    config: ReconciliationConfig,
) -> tuple[ReconciliationSnapshot, ...]:
    """Partition a frozen snapshot without separating inputs that affect one another.

    Active PO links connect invoice/GRN ledger members. Exact and suspected duplicate
    pairs are connected too, since duplicate classification is a cross-invoice rule.
    """

    parent = {item.document_id: item.document_id for item in snapshot.documents}

    def find(document_id: UUID) -> UUID:
        root = document_id
        while parent[root] != root:
            root = parent[root]
        while parent[document_id] != document_id:
            next_id = parent[document_id]
            parent[document_id] = root
            document_id = next_id
        return root

    def union(left: UUID, right: UUID) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root), key=str)
        parent[second] = first

    active_links = _active_links(snapshot)
    for link in active_links:
        union(link.from_document_id, link.to_document_id)

    invoices = tuple(
        item for item in snapshot.documents if item.document.header.doc_type is DocType.INVOICE
    )
    exact, suspected = _duplicate_map(invoices, snapshot, config)
    for duplicate_map in (exact, suspected):
        for duplicate_id, original_id in duplicate_map.items():
            union(duplicate_id, original_id)

    members: dict[UUID, set[UUID]] = {}
    for document_id in parent:
        members.setdefault(find(document_id), set()).add(document_id)
    documents_by_id = {item.document_id: item for item in snapshot.documents}
    components: list[ReconciliationSnapshot] = []
    for component_ids in sorted(members.values(), key=lambda ids: min(map(str, ids))):
        components.append(
            ReconciliationSnapshot(
                documents=tuple(
                    sorted(
                        (documents_by_id[item] for item in component_ids),
                        key=lambda item: str(item.document_id),
                    )
                ),
                links=tuple(
                    sorted(
                        (
                            link
                            for link in active_links
                            if link.from_document_id in component_ids
                            and link.to_document_id in component_ids
                        ),
                        key=lambda item: str(item.link_id),
                    )
                ),
                opening_balances=tuple(
                    sorted(
                        (
                            item
                            for item in snapshot.opening_balances
                            if item.po_document_id in component_ids
                        ),
                        key=lambda item: str(item.opening_balance_id),
                    )
                ),
                vendor_pan_by_id=snapshot.vendor_pan_by_id,
                rejected_invoice_ids=tuple(
                    sorted(
                        (item for item in snapshot.rejected_invoice_ids if item in component_ids),
                        key=str,
                    )
                ),
            )
        )
    return tuple(components)


def evaluate_components(
    snapshot: ReconciliationSnapshot,
    config: ReconciliationConfig,
    run_date: date,
) -> tuple[CaseResult, ...]:
    """Evaluate isolated link/duplicate components, preserving canonical case order."""

    documents = {item.document_id: item for item in snapshot.documents}
    results = tuple(
        result
        for component in partition_components(snapshot, config)
        for result in evaluate(component, config, run_date)
    )
    return tuple(sorted(results, key=lambda result: _component_result_sort_key(result, documents)))


def build_run_manifest(
    snapshot: ReconciliationSnapshot,
    config: ReconciliationConfig,
    run_date: date,
    config_version_id: UUID,
    engine_version: str,
) -> Manifest:
    """Create the canonical immutable input/version list for a reconciliation run."""

    return Manifest(
        engine_version=engine_version,
        rules_hash=rules_hash(config),
        config_version_id=config_version_id,
        run_date=run_date,
        documents=tuple(
            ManifestDocument(version_id=item.version_id, vendor_id=item.vendor_id)
            for item in snapshot.documents
        ),
        links=tuple(item.link_id for item in snapshot.links),
        opening_balances=tuple(item.opening_balance_id for item in snapshot.opening_balances),
        rejected_invoices=snapshot.rejected_invoice_ids,
    )


def _component_result_sort_key(
    result: CaseResult,
    documents: dict[UUID, ReconciliationDocument],
) -> tuple[date, str, str, str]:
    """Build the same stable sort key used for a whole-snapshot result."""

    if result.invoice_id is None:
        raise ValueError("reconciliation case is missing its invoice ID")
    invoice = documents[result.invoice_id]
    return (
        _doc_date(invoice.document) or date.max,
        canonicalize_document_number(_doc_no(invoice.document)),
        invoice.natural_key or str(invoice.document_id),
        str(invoice.document_id),
    )
