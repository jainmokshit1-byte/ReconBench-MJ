"""Canonical field extraction helpers shared by XML and CSV profiles."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from reconbench_core.normalize import (
    DEFAULT_CHARGE_KEYWORDS,
    classify_line_type,
    normalize_uom,
    parse_date,
    parse_decimal,
    validate_gstin,
)
from reconbench_core.parsers.types import ParseWarning
from reconbench_core.schema import (
    DocLine,
    DocType,
    Document,
    FieldEvidence,
    FieldOrigin,
    FieldSource,
    FieldValue,
    GRNHeader,
    InvoiceHeader,
    LineType,
    POHeader,
    POStatus,
    ReasonCode,
)

BASE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "doc_type": ("DOC TYPE", "DOCUMENT TYPE", "VOUCHER TYPE", "TYPE"),
    "doc_no": (
        "DOC NO",
        "DOCUMENT NO",
        "DOC NUMBER",
        "DOCUMENT NUMBER",
        "INVOICE NO",
        "INVOICE NUMBER",
        "INV NO",
        "BILL NO",
        "VOUCHER NO",
        "VOUCHER NUMBER",
        "PO NO",
        "PURCHASE ORDER NO",
        "BASIC PURCHASE ORDER NO",
        "ORDER NO",
        "GRN NO",
        "MRN NO",
        "RECEIPT NO",
    ),
    "doc_date": (
        "DATE",
        "DOC DATE",
        "DOCUMENT DATE",
        "INVOICE DATE",
        "DATED",
        "BILL DATE",
        "VOUCHER DATE",
        "ORDER DATE",
        "GRN DATE",
        "RECEIPT DATE",
    ),
    "vendor_name": (
        "VENDOR",
        "VENDOR NAME",
        "SUPPLIER",
        "SELLER",
        "PARTY",
        "PARTY NAME",
        "PARTY LEDGER NAME",
    ),
    "vendor_gstin": (
        "GSTIN",
        "GSTIN NO",
        "GSTIN UIN",
        "PARTY GSTIN",
        "SUPPLIER GSTIN",
        "VENDOR GSTIN",
    ),
    "buyer_gstin": ("BUYER GSTIN", "BUYER GSTIN UIN", "COMPANY GSTIN", "GSTIN OF BUYER"),
    "currency": ("CURRENCY", "CURRENCY CODE"),
    "taxable_total": (
        "TAXABLE TOTAL",
        "TAXABLE VALUE",
        "TAXABLE AMOUNT",
        "SUBTOTAL",
        "TOTAL TAXABLE VALUE",
    ),
    "cgst": ("CGST", "CGST TOTAL", "CENTRAL TAX"),
    "sgst": ("SGST", "SGST TOTAL", "STATE TAX", "UTGST"),
    "igst": ("IGST", "IGST TOTAL", "INTEGRATED TAX"),
    "round_off": ("ROUND OFF", "ROUNDED OFF", "ROUNDING"),
    "grand_total": (
        "GRAND TOTAL",
        "TOTAL VALUE",
        "TOTAL",
        "VOUCHER TOTAL",
        "INVOICE TOTAL",
        "TOTAL AMOUNT",
        "NET PAYABLE",
        "AMOUNT PAYABLE",
    ),
    "revision": ("REVISION", "REVISION NO", "PO REVISION"),
    "status": ("PO STATUS", "STATUS", "ORDER STATUS"),
    "ship_to": ("SHIP TO", "SHIP TO ADDRESS", "SHIP TO LOCATION"),
    "payment_terms": ("PAYMENT TERMS", "TERMS OF PAYMENT", "CREDIT TERMS"),
    "delivery_date": ("DELIVERY DATE", "EXPECTED DELIVERY", "SCHEDULED DELIVERY DATE"),
    "po_refs": (
        "PO REF",
        "PO REFERENCE",
        "PO REFERENCES",
        "PURCHASE ORDER",
        "PURCHASE ORDER REF",
        "AGAINST PO",
    ),
    "grn_refs": ("GRN REF", "GRN REFERENCE", "GRN REFERENCES", "RECEIPT REF", "MRN NO"),
    "challan_no": ("CHALLAN NO", "DELIVERY CHALLAN", "DC NO", "DELIVERY NOTE NO"),
    "supplier_invoice_no": ("SUPPLIER INVOICE NO", "VENDOR INVOICE NO", "PARTY BILL NO"),
    "e_way_bill_no": ("E WAY BILL NO", "E-WAY BILL NO", "EWAY BILL NO"),
    "irn": ("IRN", "INVOICE REFERENCE NUMBER"),
    "place_of_supply": ("PLACE OF SUPPLY", "POS", "SUPPLY STATE"),
    "line_no": ("LINE NO", "LINE NUMBER", "SR NO", "S NO", "SL NO", "ITEM NO"),
    "line_type": ("LINE TYPE", "ITEM TYPE"),
    "description": (
        "DESCRIPTION",
        "ITEM DESCRIPTION",
        "ITEM NAME",
        "STOCK ITEM NAME",
        "PRODUCT",
        "PARTICULARS",
        "NAME OF ITEM",
    ),
    "item_code": ("ITEM CODE", "PRODUCT CODE", "STOCK ITEM CODE", "SKU", "PART NO", "PART NUMBER"),
    "hsn_sac": ("HSN", "HSN CODE", "HSN SAC", "SAC", "HSN SAC CODE"),
    "uom": ("UOM", "UNIT", "UNIT OF MEASURE", "UNIT OF MEASUREMENT"),
    "quantity": (
        "QTY",
        "QUANTITY",
        "BILLED QTY",
        "BILLED QUANTITY",
        "ACTUAL QTY",
        "NOS",
        "PCS",
        "UNITS",
    ),
    "received_qty": ("RECEIVED QTY", "RECEIVED QUANTITY", "RECD QTY", "RECEIPT QTY"),
    "rejected_qty": ("REJECTED QTY", "REJECTED QUANTITY", "REJ QTY", "SHORTAGE QTY"),
    "accepted_qty": ("ACCEPTED QTY", "ACCEPTED QUANTITY", "PASSED QTY", "OK QTY"),
    "rate": ("RATE", "UNIT RATE", "UNIT PRICE", "PRICE PER UNIT", "BASIC RATE"),
    "discount": ("DISCOUNT", "LINE DISCOUNT", "DISCOUNT AMOUNT"),
    "amount": ("AMOUNT", "LINE AMOUNT", "BASIC AMOUNT", "LINE TOTAL", "TAXABLE VALUE"),
    "tax_rate": ("TAX RATE", "GST RATE", "RATE OF TAX"),
    "cgst_amount": ("CGST AMOUNT", "CENTRAL TAX AMOUNT"),
    "sgst_amount": ("SGST AMOUNT", "STATE TAX AMOUNT", "UTGST AMOUNT"),
    "igst_amount": ("IGST AMOUNT", "INTEGRATED TAX AMOUNT"),
    "po_ref": ("PO REF", "PO REFERENCE", "PURCHASE ORDER REF", "AGAINST PO"),
    "po_line_ref": ("PO LINE REF", "PO LINE NO", "PO ITEM NO", "ORDER LINE NO"),
}

_TEXT_HEADER_FIELDS = (
    "doc_no",
    "vendor_name",
    "vendor_gstin",
    "buyer_gstin",
    "currency",
    "revision",
    "ship_to",
    "payment_terms",
    "challan_no",
    "supplier_invoice_no",
    "e_way_bill_no",
    "irn",
    "place_of_supply",
)
_DECIMAL_HEADER_FIELDS = ("taxable_total", "cgst", "sgst", "igst", "round_off", "grand_total")
_DATE_HEADER_FIELDS = ("doc_date", "delivery_date")
_HEADER_FIELDS_BY_TYPE = {
    DocType.PO: {
        "doc_no",
        "doc_date",
        "vendor_name",
        "vendor_gstin",
        "buyer_gstin",
        "currency",
        "taxable_total",
        "cgst",
        "sgst",
        "igst",
        "round_off",
        "grand_total",
        "revision",
        "status",
        "ship_to",
        "payment_terms",
        "delivery_date",
    },
    DocType.GRN: {
        "doc_no",
        "doc_date",
        "vendor_name",
        "vendor_gstin",
        "buyer_gstin",
        "currency",
        "taxable_total",
        "cgst",
        "sgst",
        "igst",
        "round_off",
        "grand_total",
        "po_refs",
        "challan_no",
        "supplier_invoice_no",
    },
    DocType.INVOICE: {
        "doc_no",
        "doc_date",
        "vendor_name",
        "vendor_gstin",
        "buyer_gstin",
        "currency",
        "taxable_total",
        "cgst",
        "sgst",
        "igst",
        "round_off",
        "grand_total",
        "po_refs",
        "grn_refs",
        "challan_no",
        "e_way_bill_no",
        "irn",
        "place_of_supply",
    },
}
_DECIMAL_LINE_FIELDS = (
    "quantity",
    "received_qty",
    "rejected_qty",
    "accepted_qty",
    "rate",
    "discount",
    "amount",
    "tax_rate",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
)
_MAX_QA_ALTERNATIVE_TRIALS = 32
_TEXT_LINE_FIELDS = (
    "description",
    "item_code",
    "hsn_sac",
    "uom",
    "po_ref",
    "po_line_ref",
)


def normalize_label(value: str) -> str:
    return " ".join(re.findall(r"[A-Z0-9]+", value.upper()))


def combined_synonyms(overrides: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    combined = dict(BASE_SYNONYMS)
    for canonical, aliases in overrides.items():
        combined[canonical] = tuple(dict.fromkeys((*combined.get(canonical, ()), *aliases)))
    return combined


def alias_to_field(
    synonyms: Mapping[str, Sequence[str]], allowed_fields: set[str] | None = None
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for canonical, labels in synonyms.items():
        if allowed_fields is not None and canonical not in allowed_fields:
            continue
        normalized = normalize_label(canonical)
        aliases[normalized] = canonical
        aliases[normalized.replace(" ", "")] = canonical
        for label in labels:
            normalized = normalize_label(label)
            aliases[normalized] = canonical
            aliases[normalized.replace(" ", "")] = canonical
    return aliases


def _field[T](
    value: T,
    *,
    confidence: Decimal,
    source: FieldSource,
    snippet: str,
) -> FieldValue[T]:
    return FieldValue(
        value=value,
        confidence=confidence,
        source=source,
        evidence=FieldEvidence(snippet=snippet),
    )


def _reduce_confidence[T](field: FieldValue[T] | None, amount: Decimal) -> FieldValue[T] | None:
    if field is None:
        return None
    if field.confidence is None:
        return field
    return field.model_copy(update={"confidence": max(Decimal("0"), field.confidence - amount)})


def _raise_confidence[T](field: FieldValue[T] | None, amount: Decimal) -> FieldValue[T] | None:
    if field is None:
        return None
    if field.confidence is None:
        return field
    return field.model_copy(update={"confidence": min(Decimal("1"), field.confidence + amount)})


def _parse_decimal_field(
    raw: str | None,
    field_name: str,
    *,
    source: FieldSource,
    confidence: Decimal,
    warnings: list[ParseWarning],
    locator: str,
) -> FieldValue[Decimal] | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    if field_name == "tax_rate" and text.endswith("%"):
        text = text[:-1].strip()
    text = re.sub(r"\s*/?\s*(?:NOS|PCS|EA|UNIT|KG|KGS|MTR|M)\s*$", "", text, flags=re.I)
    try:
        value = parse_decimal(text)
    except ValueError:
        warnings.append(
            ParseWarning(
                code="INVALID_NUMBER",
                message=f"Could not read the {field_name.replace('_', ' ')} as a number.",
                locator=locator,
            )
        )
        return None
    return _field(value, confidence=confidence, source=source, snippet=raw.strip())


def _is_ambiguous_slash_date(raw: str) -> bool:
    match = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(?:\d{2}|\d{4})\s*", raw)
    if match is None:
        return False
    first, second = int(match.group(1)), int(match.group(2))
    return first <= 12 and second <= 12 and first != second


def build_document(
    *,
    doc_type: DocType,
    header_raw: Mapping[str, str],
    line_raw: Sequence[Mapping[str, str]],
    header_extras: Mapping[str, Any],
    line_extras: Sequence[Mapping[str, Any]],
    header_alternatives: Mapping[str, Sequence[str]] | None = None,
    line_alternatives: Sequence[Mapping[str, Sequence[str]]] = (),
    locator: str,
    source: FieldSource,
    origin: FieldOrigin | None = None,
    base_confidence: Decimal,
    same_file_dates: Sequence[str] = (),
    uom_aliases: Mapping[str, str] | None = None,
    charge_keywords: tuple[str, ...] = (),
) -> tuple[Document, tuple[ParseWarning, ...]]:
    """Convert raw extracted strings into a typed document and QA warnings."""

    warnings: list[ParseWarning] = []
    header_values: dict[str, Any] = {}

    for name in _TEXT_HEADER_FIELDS:
        raw = header_raw.get(name)
        if raw is None or not raw.strip():
            continue
        value = raw.strip()
        field_confidence = base_confidence
        if name in {"vendor_gstin", "buyer_gstin"}:
            validation = validate_gstin(value)
            value = validation.normalized
            if validation.is_valid:
                field_confidence = min(Decimal("1"), field_confidence + Decimal("0.03"))
            else:
                field_confidence = max(Decimal("0"), field_confidence - Decimal("0.25"))
                warnings.append(
                    ParseWarning(
                        code="GSTIN_INVALID",
                        message="A GSTIN failed format or checksum validation.",
                        locator=locator,
                    )
                )
        elif name == "currency":
            value = value.upper()
        header_values[name] = _field(
            value,
            confidence=field_confidence,
            source=source,
            snippet=raw.strip(),
        )

    for name in _DECIMAL_HEADER_FIELDS:
        parsed = _parse_decimal_field(
            header_raw.get(name),
            name,
            source=source,
            confidence=base_confidence,
            warnings=warnings,
            locator=locator,
        )
        if parsed is not None:
            header_values[name] = parsed

    for name in _DATE_HEADER_FIELDS:
        raw = header_raw.get(name)
        if raw is None or not raw.strip():
            continue
        try:
            parsed_date = parse_date(raw, same_file_dates=same_file_dates)
        except ValueError:
            warnings.append(
                ParseWarning(
                    code="INVALID_DATE",
                    message=f"Could not read the {name.replace('_', ' ')} as a date.",
                    locator=locator,
                )
            )
            continue
        field_confidence = base_confidence
        if _is_ambiguous_slash_date(raw):
            field_confidence = max(Decimal("0"), field_confidence - Decimal("0.15"))
            warnings.append(
                ParseWarning(
                    code="AMBIGUOUS_DATE",
                    message=(
                        "An ambiguous date was interpreted using the file's majority date order."
                    ),
                    locator=locator,
                )
            )
        header_values[name] = _field(
            parsed_date,
            confidence=field_confidence,
            source=source,
            snippet=raw.strip(),
        )

    for name in ("po_refs", "grn_refs"):
        raw = header_raw.get(name)
        if raw and raw.strip():
            refs = tuple(part.strip() for part in re.split(r"[;,|\n]+", raw) if part.strip())
            header_values[name] = _field(
                refs,
                confidence=base_confidence,
                source=source,
                snippet=raw.strip(),
            )

    raw_status = header_raw.get("status")
    if raw_status and doc_type is DocType.PO:
        normalized_status = raw_status.strip().casefold().replace(" ", "_")
        try:
            status = POStatus(normalized_status)
        except ValueError:
            warnings.append(
                ParseWarning(
                    code="INVALID_STATUS",
                    message="The PO status is not recognized.",
                    locator=locator,
                )
            )
        else:
            header_values["status"] = _field(
                status,
                confidence=base_confidence,
                source=source,
                snippet=raw_status.strip(),
            )

    parsed_lines: list[DocLine] = []
    for index, raw_line in enumerate(line_raw, start=1):
        line_locator = locator
        raw_number = raw_line.get("line_no", "").strip()
        try:
            line_no = int(raw_number) if raw_number else index
            if line_no < 1:
                raise ValueError
        except ValueError:
            line_no = index
            warnings.append(
                ParseWarning(
                    code="INVALID_LINE_NO",
                    message="A line number was replaced by its row order.",
                    locator=line_locator,
                )
            )

        values: dict[str, Any] = {
            "line_no": line_no,
            "extras": dict(line_extras[index - 1]) if index <= len(line_extras) else {},
        }
        for name in _TEXT_LINE_FIELDS:
            raw = raw_line.get(name)
            if raw is None or not raw.strip():
                continue
            value = raw.strip()
            if name == "uom":
                normalized_uom = normalize_uom(value, uom_aliases)
                if normalized_uom.is_known and normalized_uom.canonical is not None:
                    value = normalized_uom.canonical
                else:
                    warnings.append(
                        ParseWarning(
                            code="UOM_UNKNOWN",
                            message="An unrecognized unit of measure needs review.",
                            locator=line_locator,
                        )
                    )
            values[name] = _field(
                value,
                confidence=base_confidence,
                source=source,
                snippet=raw.strip(),
            )

        for name in _DECIMAL_LINE_FIELDS:
            parsed = _parse_decimal_field(
                raw_line.get(name),
                name,
                source=source,
                confidence=base_confidence,
                warnings=warnings,
                locator=line_locator,
            )
            if parsed is not None:
                values[name] = parsed

        raw_line_type = raw_line.get("line_type", "").strip().casefold()
        if raw_line_type:
            try:
                line_type = LineType(raw_line_type)
            except ValueError:
                line_type = classify_line_type(
                    raw_line.get("description", ""),
                    charge_keywords or DEFAULT_CHARGE_KEYWORDS,
                )
                warnings.append(
                    ParseWarning(
                        code="INVALID_LINE_TYPE",
                        message="The line type was inferred from its description.",
                        locator=line_locator,
                    )
                )
        else:
            line_type = classify_line_type(
                raw_line.get("description", ""),
                charge_keywords or DEFAULT_CHARGE_KEYWORDS,
            )
        values["line_type"] = _field(
            line_type,
            confidence=base_confidence,
            source=source,
            snippet=raw_line.get("line_type") or raw_line.get("description", ""),
        )
        parsed_lines.append(DocLine.model_validate(values))

    header_values = {
        name: value
        for name, value in header_values.items()
        if name in _HEADER_FIELDS_BY_TYPE[doc_type]
    }
    header: POHeader | GRNHeader | InvoiceHeader
    if doc_type is DocType.PO:
        header = POHeader(doc_type=DocType.PO, extras=dict(header_extras), **header_values)
    elif doc_type is DocType.GRN:
        header = GRNHeader(doc_type=DocType.GRN, extras=dict(header_extras), **header_values)
    else:
        header = InvoiceHeader(
            doc_type=DocType.INVOICE, extras=dict(header_extras), **header_values
        )

    if doc_type is DocType.GRN:
        updated_lines: list[DocLine] = []
        for line in parsed_lines:
            if (
                line.accepted_qty is None
                and line.received_qty is None
                and line.quantity is not None
            ):
                line = line.model_copy(
                    update={"received_qty": line.quantity, "accepted_qty": line.quantity}
                )
                warnings.append(
                    ParseWarning(
                        code="GRN_QTY_ASSUMED_ACCEPTED",
                        message=(
                            "GRN quantity was treated as received and accepted because "
                            "no disposition split was supplied."
                        ),
                        locator=locator,
                    )
                )
            updated_lines.append(line)
        parsed_lines = updated_lines

    qa_passed, qa_checked, qa_failure_count, qa_warnings = _check_extraction_arithmetic(
        header, parsed_lines, locator
    )
    used_alternative = False
    if qa_failure_count:
        header, parsed_lines, used_alternative = _try_numeric_alternatives(
            header,
            parsed_lines,
            header_alternatives or {},
            line_alternatives,
            source=source,
            confidence=base_confidence,
            locator=locator,
            initial_failure_count=qa_failure_count,
        )
        qa_passed, qa_checked, qa_failure_count, qa_warnings = _check_extraction_arithmetic(
            header, parsed_lines, locator
        )
        if used_alternative and qa_passed:
            warnings.append(
                ParseWarning(
                    code="EXTRACTION_QA_ALTERNATIVE_SELECTED",
                    message=(
                        "An alternate numeric reading resolved an extraction arithmetic "
                        "check. Review the selected source value."
                    ),
                    locator=locator,
                )
            )
    warnings.extend(qa_warnings)
    if qa_passed and qa_checked:
        header = _raise_header_numeric_confidence(header, Decimal("0.02"))
        parsed_lines = [
            _raise_line_numeric_confidence(line, Decimal("0.02")) for line in parsed_lines
        ]
    elif qa_warnings:
        header = _lower_header_numeric_confidence(header, Decimal("0.30"))
        parsed_lines = [
            _lower_line_numeric_confidence(line, Decimal("0.30")) for line in parsed_lines
        ]

    needs_verification = False
    reason_codes: tuple[ReasonCode, ...] = ()
    if doc_type is DocType.INVOICE:
        critical_confidences: list[Decimal] = []
        missing_critical = False
        if header.doc_no is None or header.doc_no.confidence is None:
            missing_critical = True
        else:
            critical_confidences.append(header.doc_no.confidence)
        if header.doc_date is None or header.doc_date.confidence is None:
            missing_critical = True
        else:
            critical_confidences.append(header.doc_date.confidence)
        vendor_fields = [
            field for field in (header.vendor_name, header.vendor_gstin) if field is not None
        ]
        vendor_confidences = [
            field.confidence for field in vendor_fields if field.confidence is not None
        ]
        if not vendor_confidences:
            missing_critical = True
        else:
            critical_confidences.append(max(vendor_confidences))
        eligible_line_count = 0
        for line in parsed_lines:
            quantity_field = line.quantity
            numeric_field = line.rate if line.rate is not None else line.amount
            if quantity_field is None or numeric_field is None:
                continue
            if quantity_field.confidence is None or numeric_field.confidence is None:
                missing_critical = True
                continue
            eligible_line_count += 1
            critical_confidences.extend((quantity_field.confidence, numeric_field.confidence))
        if eligible_line_count == 0:
            missing_critical = True
        if header.grand_total is None or header.grand_total.confidence is None:
            missing_critical = True
        else:
            critical_confidences.append(header.grand_total.confidence)
        document_confidence = min(critical_confidences, default=Decimal("0"))
        needs_verification = missing_critical or document_confidence < Decimal("0.70")
        if needs_verification:
            reason_codes = (ReasonCode.LOW_CONFIDENCE_DATA,)
            warnings.append(
                ParseWarning(
                    code="CRITICAL_FIELDS_NEED_VERIFICATION",
                    message=(
                        "One or more critical invoice fields are missing or below 0.70 confidence."
                    ),
                    locator=locator,
                )
            )

    document = Document(
        header=header,
        lines=tuple(parsed_lines),
        source_locator=locator,
        needs_verification=needs_verification,
        reason_codes=reason_codes,
    )
    if origin is not None:
        header = document.header.model_copy(
            update={
                name: value.model_copy(update={"origin": origin})
                for name in type(document.header).model_fields
                if isinstance((value := getattr(document.header, name)), FieldValue)
            }
        )
        lines = tuple(
            line.model_copy(
                update={
                    name: value.model_copy(update={"origin": origin})
                    for name in type(line).model_fields
                    if isinstance((value := getattr(line, name)), FieldValue)
                }
            )
            for line in document.lines
        )
        document = document.model_copy(update={"header": header, "lines": lines})
    return document, tuple(warnings)


ArithmeticLine = tuple[int, Decimal | None, Decimal | None, Decimal | None, Decimal | None]
"""``(line_no, quantity, rate, discount, amount)`` with plain values."""


def arithmetic_failures(
    lines: Sequence[ArithmeticLine],
    *,
    taxable_total: Decimal | None,
    taxes: Sequence[Decimal | None],
    round_off: Decimal | None,
    grand_total: Decimal | None,
) -> tuple[bool, tuple[str, ...]]:
    """TR-PAR-5 checks on plain values (±₹1): ``(checks_run, failures)``."""

    failures: list[str] = []
    checks_run = False
    for line_no, quantity, rate, discount, amount in lines:
        if quantity is not None and rate is not None and amount is not None:
            checks_run = True
            expected = quantity * rate - (discount or Decimal("0"))
            if abs(expected - amount) > Decimal("1"):
                failures.append(f"line {line_no} amount")

    amounts = [amount for *_rest, amount in lines if amount is not None]
    if amounts and taxable_total is not None:
        checks_run = True
        if abs(sum(amounts, Decimal("0")) - taxable_total) > Decimal("1"):
            failures.append("taxable total")

    if grand_total is not None and taxable_total is not None:
        checks_run = True
        tax = sum((value for value in taxes if value is not None), Decimal("0"))
        expected_total = taxable_total + tax + (round_off or Decimal("0"))
        if abs(expected_total - grand_total) > Decimal("1"):
            failures.append("grand total")
    return checks_run, tuple(failures)


def _check_extraction_arithmetic(
    header: POHeader | GRNHeader | InvoiceHeader,
    lines: Sequence[DocLine],
    locator: str,
) -> tuple[bool, bool, int, tuple[ParseWarning, ...]]:
    def plain(field: FieldValue[Decimal] | None) -> Decimal | None:
        return field.value if field is not None else None

    checks_run, failures = arithmetic_failures(
        [
            (
                line.line_no,
                plain(line.quantity),
                plain(line.rate),
                plain(line.discount),
                plain(line.amount),
            )
            for line in lines
        ],
        taxable_total=plain(header.taxable_total),
        taxes=[plain(header.cgst), plain(header.sgst), plain(header.igst)],
        round_off=plain(header.round_off),
        grand_total=plain(header.grand_total),
    )
    if not failures:
        return True, checks_run, 0, ()
    message = "Extraction arithmetic did not reconcile: " + ", ".join(failures) + "."
    return (
        False,
        True,
        len(failures),
        (ParseWarning(code="EXTRACTION_QA_FAILED", message=message, locator=locator),),
    )


def _try_numeric_alternatives(
    header: POHeader | GRNHeader | InvoiceHeader,
    lines: list[DocLine],
    header_alternatives: Mapping[str, Sequence[str]],
    line_alternatives: Sequence[Mapping[str, Sequence[str]]],
    *,
    source: FieldSource,
    confidence: Decimal,
    locator: str,
    initial_failure_count: int,
) -> tuple[POHeader | GRNHeader | InvoiceHeader, list[DocLine], bool]:
    """Try bounded duplicate-column readings, retaining only QA improvements."""

    candidates: list[tuple[int | None, str, str]] = []
    for name, values in header_alternatives.items():
        if name in _DECIMAL_HEADER_FIELDS:
            candidates.extend((None, name, raw) for raw in values)
    for line_index, alternatives in enumerate(line_alternatives):
        for name, values in alternatives.items():
            if name in _DECIMAL_LINE_FIELDS:
                candidates.extend((line_index, name, raw) for raw in values)

    best_header = header
    best_lines = list(lines)
    best_failure_count = initial_failure_count
    selected = False
    remaining = candidates[:_MAX_QA_ALTERNATIVE_TRIALS]
    attempts = 0
    while remaining and attempts < _MAX_QA_ALTERNATIVE_TRIALS:
        next_round: list[tuple[int | None, str, str]] = []
        improved = False
        for candidate_line_index, name, raw in remaining:
            if attempts >= _MAX_QA_ALTERNATIVE_TRIALS:
                break
            attempts += 1
            parse_warnings: list[ParseWarning] = []
            parsed = _parse_decimal_field(
                raw,
                name,
                source=source,
                confidence=confidence,
                warnings=parse_warnings,
                locator=locator,
            )
            if parsed is None:
                continue
            trial_header = best_header
            trial_lines = list(best_lines)
            if candidate_line_index is None:
                trial_header = best_header.model_copy(update={name: parsed})
            elif candidate_line_index < len(trial_lines):
                trial_lines[candidate_line_index] = trial_lines[candidate_line_index].model_copy(
                    update={name: parsed}
                )
            else:
                continue
            _, _, failure_count, _ = _check_extraction_arithmetic(
                trial_header, trial_lines, locator
            )
            if failure_count < best_failure_count:
                best_header = trial_header
                best_lines = trial_lines
                best_failure_count = failure_count
                selected = True
                improved = True
            else:
                next_round.append((candidate_line_index, name, raw))
        if not improved:
            break
        remaining = next_round
    return best_header, best_lines, selected


def _adjust_field_confidence[T](
    field: FieldValue[T] | None, amount: Decimal, *, increase: bool
) -> FieldValue[T] | None:
    if field is None:
        return None
    if field.confidence is None:
        return field
    value = field.confidence + amount if increase else field.confidence - amount
    return field.model_copy(update={"confidence": min(Decimal("1"), max(Decimal("0"), value))})


def _header_numeric_updates(
    header: POHeader | GRNHeader | InvoiceHeader, amount: Decimal, *, increase: bool
) -> dict[str, Any]:
    return {
        name: _adjust_field_confidence(getattr(header, name), amount, increase=increase)
        for name in ("taxable_total", "cgst", "sgst", "igst", "round_off", "grand_total")
    }


def _line_numeric_updates(line: DocLine, amount: Decimal, *, increase: bool) -> dict[str, Any]:
    return {
        name: _adjust_field_confidence(getattr(line, name), amount, increase=increase)
        for name in _DECIMAL_LINE_FIELDS
    }


def _raise_header_numeric_confidence(
    header: POHeader | GRNHeader | InvoiceHeader, amount: Decimal
) -> POHeader | GRNHeader | InvoiceHeader:
    return header.model_copy(update=_header_numeric_updates(header, amount, increase=True))


def _lower_header_numeric_confidence(
    header: POHeader | GRNHeader | InvoiceHeader, amount: Decimal
) -> POHeader | GRNHeader | InvoiceHeader:
    return header.model_copy(update=_header_numeric_updates(header, amount, increase=False))


def _raise_line_numeric_confidence(line: DocLine, amount: Decimal) -> DocLine:
    return line.model_copy(update=_line_numeric_updates(line, amount, increase=True))


def _lower_line_numeric_confidence(line: DocLine, amount: Decimal) -> DocLine:
    return line.model_copy(update=_line_numeric_updates(line, amount, increase=False))
