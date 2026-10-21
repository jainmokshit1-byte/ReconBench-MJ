"""Safe, streaming XML profile parser for Tally and generic document exports."""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from lxml import etree  # type: ignore[import-untyped]

from reconbench_core.normalize import parse_decimal
from reconbench_core.parsers.common import (
    _HEADER_FIELDS_BY_TYPE,
    alias_to_field,
    build_document,
    combined_synonyms,
    normalize_label,
)
from reconbench_core.parsers.parties import guard_result
from reconbench_core.parsers.tally import (
    Sanitized,
    is_native_voucher,
    is_order_number,
    plain_decimal,
    removal_message,
    sanitize,
    voucher_facts,
)
from reconbench_core.parsers.types import ParseResult, ParserHints, ParseWarning
from reconbench_core.schema import (
    DocType,
    Document,
    FieldOrigin,
    FieldSource,
    FileFormat,
    ReasonCode,
    WarnLevel,
)

_DOCUMENT_TAGS = {
    "VOUCHER",
    "INVOICE",
    "TAXINVOICE",
    "PURCHASEORDER",
    "PURCHASEORDERDOCUMENT",
    "PO",
    "GRN",
    "GOODSRECEIPT",
    "GOODSRECEIPTNOTE",
    "MATERIALRECEIPTNOTE",
    "MRN",
}
_LINE_TAGS = {
    "LINE",
    "ITEMLINE",
    "DETAIL",
    "DETAILS",
    "ITEM",
    "STOCKITEM",
    "INVENTORYENTRY",
    "INVENTORYENTRIESLIST",
    "ALLINVENTORYENTRIESLIST",
    "ITEMS",
    "ITEMDETAILS",
}
_HEADER_FIELDS = {
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
    "po_refs",
    "grn_refs",
    "challan_no",
    "supplier_invoice_no",
    "e_way_bill_no",
    "irn",
    "place_of_supply",
}
_LINE_FIELDS = {
    "line_no",
    "line_type",
    "description",
    "item_code",
    "hsn_sac",
    "uom",
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
    "po_ref",
    "po_line_ref",
}
_DBC_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_DBC_MEASURE = re.compile(
    rf"\s*(?P<number>{_DBC_NUMBER})(?:\s*(?:/|\s)\s*(?P<uom>[A-Za-z][A-Za-z0-9._-]*))?\s*"
)
# Narration rows under a Tally stock item: "SN: X,Y" serial lists, bare serials
# ("RC310CKKN3U31254900038") and part codes ("RC-310C-KKN3-U3").
_DBC_SERIAL_LABEL = re.compile(r"^\s*(?:S\.?\s*N(?:O)?\.?|SERIAL(?:\s+NO\.?)?)\s*[:#-]\s*", re.I)
_DBC_SERIAL = re.compile(r"^(?=(?:[A-Z]*\d){4})[A-Z0-9]{8,}$", re.I)
_DBC_PART_CODE = re.compile(r"^(?=.*\d)[A-Z0-9]+(?:[-/.][A-Z0-9]+)+$", re.I)


def _dbc_narration(texts: list[str]) -> tuple[str | None, str | None, list[str], list[str]]:
    """Split an item's narration rows into (item name, part code, serials, other text)."""

    name: str | None = None
    code: str | None = None
    serials: list[str] = []
    other: list[str] = []
    in_serials = False
    for raw in texts:
        text = " ".join(raw.split())
        labelled = _DBC_SERIAL_LABEL.match(text)
        tokens = [token for token in re.split(r"[,;\s]+", text) if token]
        if labelled:
            in_serials = True
            serials.extend(t for t in re.split(r"[,;\s]+", text[labelled.end() :]) if t)
        elif (
            tokens
            and all(_DBC_SERIAL.match(token) for token in tokens)
            and (in_serials or len(tokens) > 1 or name is not None)
        ):
            serials.extend(tokens)
        elif len(tokens) == 1 and code is None and name is not None and _DBC_PART_CODE.match(text):
            code = text
            in_serials = False
        elif name is None:
            name = text
        else:
            other.append(text)
            in_serials = False
    return name, code, serials, other


def _tag_name(element: etree._Element) -> str:
    if not isinstance(element.tag, str):
        return ""
    return normalize_label(etree.QName(element).localname).replace(" ", "")


def _text(element: etree._Element) -> str:
    return " ".join(part.strip() for part in element.itertext() if part.strip())


def _document_type(element: etree._Element, hints: ParserHints) -> DocType | None:
    if hints.doc_type is not None:
        return hints.doc_type
    tag = _tag_name(element)
    type_text = " ".join(
        _text(node)
        for node in element.iter()
        if _tag_name(node) in {"VOUCHERTYPENAME", "DOCTYPE", "DOCUMENTTYPE"}
    ).casefold()
    combined = f"{tag} {type_text}".casefold()
    if "purchase order" in combined or "purchaseorder" in combined or tag == "PO":
        return DocType.PO
    if any(
        word in combined
        for word in (
            "goods receipt",
            "goodsreceipt",
            "material receipt",
            "materialreceipt",
            "receipt note",
            "grn",
            "mrn",
        )
    ):
        return DocType.GRN
    if any(word in combined for word in ("invoice", "bill", "voucher")):
        return DocType.INVOICE
    return None


def _line_nodes(element: etree._Element) -> list[etree._Element]:
    candidates = [
        node for node in element.iter() if node is not element and _tag_name(node) in _LINE_TAGS
    ]
    candidate_set = set(candidates)
    return [
        node
        for node in candidates
        if not any(descendant in candidate_set for descendant in node.iterdescendants())
    ]


def _inside_line_node(node: etree._Element, line_nodes: set[etree._Element]) -> bool:
    return any(ancestor in line_nodes for ancestor in node.iterancestors())


def _raw_fields(
    element: etree._Element,
    allowed: set[str],
    aliases: Mapping[str, str],
    *,
    line_nodes: set[etree._Element] | None = None,
    alternatives: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in element.iter():
        if line_nodes and _inside_line_node(node, line_nodes):
            continue
        canonical = aliases.get(_tag_name(node))
        value = _text(node)
        if canonical in allowed and value:
            if canonical not in values:
                values[canonical] = value
            elif canonical in {"po_refs", "grn_refs"}:
                references = [part.strip() for part in values[canonical].split(";")]
                if value not in references:
                    values[canonical] = f"{values[canonical]};{value}"
            elif alternatives is not None and value != values[canonical]:
                alternative_values = alternatives.setdefault(canonical, [])
                if value not in alternative_values:
                    alternative_values.append(value)
    return values


def _element_value(element: etree._Element) -> Any:
    children = [child for child in element if isinstance(child.tag, str)]
    if not children:
        return _text(element)
    result: dict[str, Any] = {}
    for child in children:
        key = etree.QName(child).localname
        value = _element_value(child)
        if key in result:
            previous = result[key]
            result[key] = [*previous, value] if isinstance(previous, list) else [previous, value]
        else:
            result[key] = value
    return result


def _extras(
    element: etree._Element,
    known_tags: set[str],
    *,
    excluded: set[etree._Element] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for child in element:
        if not isinstance(child.tag, str) or (excluded and child in excluded):
            continue
        key = etree.QName(child).localname
        if _tag_name(child) not in known_tags:
            value = _element_value(child)
            if key in result:
                prior = result[key]
                result[key] = [*prior, value] if isinstance(prior, list) else [prior, value]
            else:
                result[key] = value
        elif len(child):
            nested = _extras(child, known_tags, excluded=excluded)
            for nested_key, value in nested.items():
                if nested_key not in result:
                    result[nested_key] = value
    return result


def _custom_xpath_value(element: etree._Element, expression: str) -> str | None:
    result = element.xpath(expression)
    if not isinstance(result, list):
        return str(result).strip() or None
    for item in result:
        text = _text(item) if isinstance(item, etree._Element) else str(item).strip()
        if text:
            return text
    return None


def _apply_native_tally(
    element: etree._Element,
    doc_type: DocType,
    header_raw: dict[str, str],
    raw_lines: list[dict[str, str]],
    raw_nodes: list[etree._Element | None],
    line_extras: list[dict[str, Any]],
    line_alternatives: list[dict[str, list[str]]],
    *,
    custom_fields: set[str],
    locator: str,
) -> list[ParseWarning]:
    """Native Tally voucher (WI-IMP1, D95): item text, signs, ledger totals, GRN orders."""

    facts = voucher_facts(element)
    warnings: list[ParseWarning] = []
    signs: list[str] = []
    for index, node in enumerate(raw_nodes):
        line = facts.lines.get(node) if node is not None else None
        if line is None:
            continue
        raw = raw_lines[index]
        # The item text is BASICUSERDESCRIPTION; the stock item name stays the item name.
        if line.user_description:
            raw["description"] = line.user_description
        if line.stock_item:
            line_extras[index] = {**line_extras[index], "stock_item_name": line.stock_item}
        if line.gst_rate is not None:
            raw["tax_rate"] = plain_decimal(line.gst_rate)
        amount = raw.get("amount", "")
        if line.deemed_positive and amount.startswith("-"):
            raw["amount"] = amount[1:].strip()
            signs.append(f"line {index + 1} amount {amount!r}")
            alternatives = line_alternatives[index].get("amount")
            if alternatives:
                line_alternatives[index]["amount"] = [
                    value[1:].strip() if value.startswith("-") else value for value in alternatives
                ]
        if doc_type is DocType.GRN and line.order_numbers and not raw.get("po_ref"):
            raw["po_ref"] = line.order_numbers[0]
    if signs:
        warnings.append(
            ParseWarning(
                code="TALLY_SIGN_NORMALISED",
                level=WarnLevel.INFO,
                message=(
                    "Tally exports purchase-side amounts as negative debits; stored as "
                    "magnitudes: " + "; ".join(signs) + "."
                ),
                locator=locator,
            )
        )
    if doc_type in {DocType.PO, DocType.GRN}:
        totals = facts.totals()
        if totals is not None:
            filled = [name for name in totals if not header_raw.get(name)]
            for name in filled:
                header_raw[name] = plain_decimal(totals[name])
            if filled:
                warnings.append(
                    ParseWarning(
                        code="TALLY_TOTALS_FROM_LEDGERS",
                        level=WarnLevel.INFO,
                        message=(
                            f"Header totals ({', '.join(sorted(filled))}) read from the party "
                            "and tax ledger entries; they close against the lines within 1."
                        ),
                        locator=locator,
                    )
                )
        elif facts.party_entries == 1 and facts.party_amount is not None:
            if not header_raw.get("grand_total"):
                header_raw["grand_total"] = plain_decimal(abs(facts.party_amount))
            warnings.append(
                ParseWarning(
                    code="TALLY_TOTALS_NOT_CLOSED",
                    message=(
                        "The party ledger amount does not close against the lines, tax and "
                        "round-off ledgers; only the voucher total was read."
                    ),
                    locator=locator,
                )
            )
    if doc_type is DocType.GRN:
        # A Receipt Note names its purchase order in REFERENCE and in each line's ORDERNO.
        named = list(dict.fromkeys(ref for ref in (facts.reference, *facts.order_numbers) if ref))
        if "po_refs" not in custom_fields:
            # A part without a digit is not an order number (Tally's "Not Applicable").
            existing = [
                part.strip()
                for part in header_raw.get("po_refs", "").split(";")
                if is_order_number(part)
            ]
            merged = list(dict.fromkeys(part for part in (*existing, *named) if part))
            if merged:
                header_raw["po_refs"] = ";".join(merged)
            named = merged
        if len(named) > 1:
            warnings.append(
                ParseWarning(
                    code="TALLY_GRN_MULTIPLE_PO_REFS",
                    message=(
                        f"This receipt note names {len(named)} purchase orders (REFERENCE and "
                        "line ORDERNO); check which order each line belongs to."
                    ),
                    locator=locator,
                )
            )
    return warnings


def _parse_one(
    element: etree._Element,
    hints: ParserHints,
    synonyms: Mapping[str, tuple[str, ...]],
    known_tags: set[str],
    same_file_dates: tuple[str, ...],
) -> tuple[Any, tuple[ParseWarning, ...]]:
    doc_type = _document_type(element, hints)
    if doc_type is None:
        raise ValueError("document type could not be inferred from the XML record")
    header_aliases = alias_to_field(synonyms, _HEADER_FIELDS_BY_TYPE[doc_type])
    line_aliases = alias_to_field(synonyms, _LINE_FIELDS)
    locator = element.getroottree().getpath(element)
    line_nodes = _line_nodes(element)
    line_node_set = set(line_nodes)

    header_alternatives: dict[str, list[str]] = {}
    custom_fields: set[str] = set()
    header_raw = _raw_fields(
        element,
        _HEADER_FIELDS,
        header_aliases,
        line_nodes=line_node_set,
        alternatives=header_alternatives,
    )
    for canonical, expression in hints.custom_xpaths.items():
        if canonical in _HEADER_FIELDS:
            try:
                custom_value = _custom_xpath_value(element, expression)
            except (etree.XPathError, ValueError):
                custom_value = None
            if custom_value:
                header_raw[canonical] = custom_value
                header_alternatives.pop(canonical, None)
                custom_fields.add(canonical)

    raw_lines: list[dict[str, str]] = []
    raw_nodes: list[etree._Element | None] = []
    line_alternatives: list[dict[str, list[str]]] = []
    line_extras: list[dict[str, Any]] = []
    for line_node in line_nodes:
        raw_line_alternatives: dict[str, list[str]] = {}
        raw_line = _raw_fields(
            line_node,
            _LINE_FIELDS,
            line_aliases,
            alternatives=raw_line_alternatives,
        )
        if raw_line:
            _unit_from_measures(raw_line)
            raw_lines.append(raw_line)
            raw_nodes.append(line_node)
            line_alternatives.append(raw_line_alternatives)
            line_extras.append(_extras(line_node, known_tags))

    if not raw_lines:
        single_line_alternatives: dict[str, list[str]] = {}
        single_line = _raw_fields(
            element,
            _LINE_FIELDS,
            line_aliases,
            line_nodes=line_node_set,
            alternatives=single_line_alternatives,
        )
        if any(name in single_line for name in ("description", "quantity", "rate", "amount")):
            raw_lines.append(single_line)
            raw_nodes.append(None)
            line_alternatives.append(single_line_alternatives)
            line_extras.append({})

    tally_warnings: list[ParseWarning] = []
    if is_native_voucher(element):
        tally_warnings = _apply_native_tally(
            element,
            doc_type,
            header_raw,
            raw_lines,
            raw_nodes,
            line_extras,
            line_alternatives,
            custom_fields=custom_fields,
            locator=locator,
        )

    document, warnings = build_document(
        doc_type=doc_type,
        header_raw=header_raw,
        line_raw=raw_lines,
        header_extras=_extras(element, known_tags, excluded=line_node_set),
        line_extras=line_extras,
        header_alternatives=header_alternatives,
        line_alternatives=line_alternatives,
        locator=locator,
        source=FieldSource.RULE,
        origin=FieldOrigin.ERP_IMPORT,
        base_confidence=Decimal("0.95"),
        same_file_dates=same_file_dates,
        uom_aliases=hints.uom_aliases,
        charge_keywords=hints.charge_keywords,
    )
    return document, (*tally_warnings, *warnings)


def _dbc_rows(root: etree._Element) -> list[list[etree._Element]]:
    """Split the flat DBC export into its repeated row groups."""

    rows: list[list[etree._Element]] = []
    current: list[etree._Element] = []
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if _tag_name(child) == "DBCFIXED":
            if current:
                rows.append(current)
            current = [child]
        elif current:
            current.append(child)
    if current:
        rows.append(current)
    return rows


def _dbc_values(rows: list[etree._Element]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for node in rows:
        tag = _tag_name(node)
        if tag == "DBCFIXED":
            fields = [child for child in node if isinstance(child.tag, str)]
        else:
            fields = [node]
        for field in fields:
            value = _text(field)
            if value:
                values.setdefault(_tag_name(field), []).append(value)
    return values


def _unit_from_measures(raw_line: dict[str, str]) -> None:
    """Take the unit from Tally-style measures such as ``10 Nos`` or ``1003.00/Nos``.

    Tally writes the unit inside ACTUALQTY/BILLEDQTY/RATE rather than in its own
    tag. The unit is only filled when the line has none and the measures agree.
    """

    if raw_line.get("uom"):
        return
    units = {
        unit.casefold(): unit
        for unit in (
            _dbc_measure(raw_line.get("quantity"))[1],
            _dbc_measure(raw_line.get("rate"))[1],
        )
        if unit
    }
    if len(units) == 1:
        raw_line["uom"] = next(iter(units.values()))


def _dbc_measure(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    match = _DBC_MEASURE.fullmatch(value)
    if match is None:
        return None, None
    try:
        parsed = parse_decimal(match.group("number"))
    except ValueError:
        return None, None
    return format(parsed, "f"), match.group("uom")


def _dbc_amount(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return abs(parse_decimal(value))
    except ValueError:
        return None


def _dbc_first(values: dict[str, list[str]], tag: str) -> str | None:
    return next((value.strip() for value in values.get(tag, []) if value.strip()), None)


def _dbc_totals_reconcile(rows: list[dict[str, list[str]]], lines: list[dict[str, str]]) -> bool:
    """Lines sum to the voucher's taxable amount, and its ledger amounts to its gross."""

    taxable = _dbc_amount(_dbc_first(rows[0], "DBCAMOUNT"))
    gross = _dbc_amount(_dbc_first(rows[0], "DBCGROSSAMT"))
    if taxable is None or gross is None or not lines:
        return False
    amounts = [_dbc_amount(line.get("amount")) for line in lines]
    if any(amount is None for amount in amounts):
        return False
    if sum((amount or Decimal("0") for amount in amounts), Decimal("0")) != taxable:
        return False
    ledger: list[Decimal] = []
    for values in rows:
        for raw in values.get("DBCLEDAMT", []):
            if raw.strip():
                amount = _dbc_amount(raw.strip())
                if amount is None:
                    return False
                ledger.append(amount)
    return bool(ledger) and sum(ledger, Decimal("0")) == gross


def _parse_dbc_flat(root: etree._Element, hints: ParserHints, tag_names: set[str]) -> ParseResult:
    """Parse the flat DBC* report shape without inventing absent item data."""

    signature = hashlib.sha256(
        "|".join(("tally-dbc-flat-v1", *sorted(tag_names))).encode("utf-8")
    ).hexdigest()
    if hints.doc_type is None:
        return ParseResult(
            success=False,
            file_format=FileFormat.XML,
            layout_signature=signature,
            reason_code="DOC_TYPE_REQUIRED",
            warnings=(
                ParseWarning(
                    code="DOC_TYPE_REQUIRED",
                    message="Select PO, GRN, or Invoice upload zone for this DBC export.",
                ),
            ),
        )

    row_groups = _dbc_rows(root)
    record_groups: list[tuple[int, list[dict[str, list[str]]]]] = []
    current_rows: list[dict[str, list[str]]] = []
    current_start = 0
    skipped_before_first = 0
    for index, row in enumerate(row_groups, start=1):
        values = _dbc_values(row)
        doc_numbers = values.get("DBCVCHNO", [])
        doc_number = next((value.strip() for value in doc_numbers if value.strip()), None)
        if doc_number:
            if current_rows:
                record_groups.append((current_start, current_rows))
            current_start = index
            current_rows = []
        elif not current_rows:
            skipped_before_first += 1
            continue
        current_rows.append(values)
    if current_rows:
        record_groups.append((current_start, current_rows))

    if not record_groups:
        return ParseResult(
            success=False,
            file_format=FileFormat.XML,
            layout_signature=signature,
            reason_code="NO_DOCUMENTS",
            warnings=(
                ParseWarning(
                    code="NO_DOCUMENTS",
                    message="The DBC report contains no voucher-numbered document rows.",
                ),
            ),
        )

    documents: list[Document] = []
    warnings: list[ParseWarning] = []
    if skipped_before_first:
        warnings.append(
            ParseWarning(
                code="DBC_ROWS_WITHOUT_VOUCHER",
                message="Rows before the first voucher number were excluded from documents.",
            )
        )

    for record_number, (start_row, rows) in enumerate(record_groups, start=1):
        header_values: dict[str, str] = {}
        first = rows[0]
        for target, source in (
            ("doc_no", "DBCVCHNO"),
            ("doc_date", "DBCDATE"),
            ("vendor_name", "DBCPARTY"),
            ("vendor_gstin", "DBCGSTIN"),
            ("grand_total", "DBCGROSSAMT"),
        ):
            raw = next((value.strip() for value in first.get(source, []) if value.strip()), None)
            if raw is not None:
                header_values[target] = raw

        line_raw: list[dict[str, str]] = []
        line_extras: list[dict[str, Any]] = []
        narrations: list[list[str]] = []
        for row_number, values in enumerate(rows, start=start_row):
            quantity_raw = next(
                (value.strip() for value in values.get("DBCQTY", []) if value.strip()), None
            )
            rate_raw = next(
                (value.strip() for value in values.get("DBCRATE", []) if value.strip()), None
            )
            if quantity_raw is None or rate_raw is None:
                # A row without quantity and rate under an item is its narration.
                party = next((v.strip() for v in values.get("DBCPARTY", []) if v.strip()), None)
                if narrations and party and row_number != start_row:
                    narrations[-1].append(party)
                continue
            quantity, quantity_uom = _dbc_measure(quantity_raw)
            rate, rate_uom = _dbc_measure(rate_raw)
            if quantity is None or rate is None:
                warnings.append(
                    ParseWarning(
                        code="DBC_MEASURE_UNREADABLE",
                        message="A DBC quantity or rate was retained as raw evidence for review.",
                        locator=f"/ENVELOPE/DBC_RECORD[{record_number}]/ROW[{row_number}]",
                    )
                )
                continue
            raw_line: dict[str, str] = {"quantity": quantity, "rate": rate}
            # On the voucher's first row DBCPARTY is the party, not a stock item.
            description = (
                None
                if row_number == start_row
                else next(
                    (value.strip() for value in values.get("DBCPARTY", []) if value.strip()),
                    None,
                )
            )
            if description:
                raw_line["description"] = description
            uom = quantity_uom or rate_uom
            if quantity_uom and rate_uom and quantity_uom.casefold() != rate_uom.casefold():
                warnings.append(
                    ParseWarning(
                        code="DBC_UOM_CONFLICT",
                        message="Quantity and rate units differ; the line needs manual review.",
                        locator=f"/ENVELOPE/DBC_RECORD[{record_number}]/ROW[{row_number}]",
                    )
                )
            if uom:
                raw_line["uom"] = uom
            accounting_amount_raw = next(
                (value.strip() for value in values.get("DBCAMOUNT", []) if value.strip()), None
            )
            amount_reconciles = False
            if accounting_amount_raw is not None:
                try:
                    accounting_amount = abs(parse_decimal(accounting_amount_raw))
                except ValueError:
                    accounting_amount = None
                extended_amount = Decimal(quantity) * Decimal(rate)
                if accounting_amount is not None and abs(accounting_amount - extended_amount) <= 1:
                    raw_line["amount"] = format(accounting_amount, "f")
                    amount_reconciles = True
                else:
                    warnings.append(
                        ParseWarning(
                            code="DBC_AMOUNT_CONFLICT",
                            message=(
                                "The DBC accounting amount does not agree with quantity times "
                                "rate; the source amount was retained for review."
                            ),
                            locator=f"/ENVELOPE/DBC_RECORD[{record_number}]/ROW[{row_number}]",
                        )
                    )
            line_raw.append(raw_line)
            narrations.append([])
            line_extras.append(
                {
                    "dbc_stock_item": description,
                    "dbc_quantity_raw": quantity_raw,
                    "dbc_rate_raw": rate_raw,
                    "dbc_accounting_amount_raw": accounting_amount_raw,
                    "dbc_alternate_units_raw": values.get("DBCALTUNITS", []),
                    "dbc_source_row": row_number,
                    "dbc_line_description_missing": description is None,
                    "dbc_amount_reconciles_to_quantity_times_rate": amount_reconciles,
                }
            )

        for raw_line, extras, texts in zip(line_raw, line_extras, narrations, strict=True):
            name, code, serials, other = _dbc_narration(texts)
            if name:
                # The stock item is often a category ("CABINET"); the item is named below it.
                raw_line["description"] = name
            if code:
                raw_line.setdefault("item_code", code)
            if serials:
                extras["serials"] = serials
            if other:
                extras["dbc_narration"] = other
        reconciled = _dbc_totals_reconcile(rows, line_raw)

        record_locator = f"/ENVELOPE/DBC_RECORD[{record_number}]"
        document, document_warnings = build_document(
            doc_type=hints.doc_type,
            header_raw=header_values,
            line_raw=line_raw,
            header_extras={
                "dbc_buyer_name_raw": next(
                    (value for value in first.get("DBCBUYERNAME", []) if value), None
                ),
                "dbc_ledger_amounts_raw": [
                    {
                        "row": row_number,
                        "values": values.get("DBCLEDAMT", []),
                    }
                    for row_number, values in enumerate(rows, start=start_row)
                    if values.get("DBCLEDAMT")
                ],
                "dbc_other_row_values_raw": [
                    {
                        "row": row_number,
                        "alternate_units": values.get("DBCALTUNITS", []),
                        "buyer_name": values.get("DBCBUYERNAME", []),
                        "gross_amount": values.get("DBCGROSSAMT", []),
                    }
                    for row_number, values in enumerate(rows, start=start_row)
                    if any(
                        values.get(name) for name in ("DBCALTUNITS", "DBCBUYERNAME", "DBCGROSSAMT")
                    )
                ],
                "dbc_source_fields_raw": [
                    {"row": row_number, "fields": values}
                    for row_number, values in enumerate(rows, start=start_row)
                ],
                "dbc_line_descriptions_present": any(
                    bool(line.get("description")) for line in line_raw
                ),
                "dbc_explicit_po_reference_present": False,
            },
            line_extras=line_extras,
            locator=record_locator,
            source=FieldSource.RULE,
            origin=FieldOrigin.ERP_IMPORT,
            base_confidence=Decimal("0.95"),
            uom_aliases=hints.uom_aliases,
            charge_keywords=hints.charge_keywords,
        )
        # A digital export is trusted as read when every line names its item and the
        # voucher's own totals agree; otherwise a person checks it.
        unnamed = not line_raw or any(not line.get("description") for line in line_raw)
        reasons = (
            *((ReasonCode.ITEM_UNSURE,) if unnamed else ()),
            *(() if reconciled else (ReasonCode.LOW_CONFIDENCE_DATA,)),
            *((ReasonCode.NO_PO_LINK,) if hints.doc_type is DocType.INVOICE else ()),
        )
        if not reconciled:
            warnings.append(
                ParseWarning(
                    code="DBC_TOTALS_UNRECONCILED",
                    message=(
                        "The voucher's line amounts, taxable amount and ledger amounts could "
                        "not be reconciled with its gross amount; review the document."
                    ),
                    locator=record_locator,
                )
            )
        if reasons:
            document = document.model_copy(
                update={
                    "needs_verification": True,
                    "reason_codes": tuple(dict.fromkeys((*document.reason_codes, *reasons))),
                }
            )
        documents.append(document)
        warnings.extend(document_warnings)
        if line_raw and any("description" not in line for line in line_raw):
            warnings.append(
                ParseWarning(
                    code="DBC_LINE_DESCRIPTION_MISSING",
                    message=(
                        "One or more DBC inventory rows have no item description; automatic "
                        "item matching for those rows is unsafe and they require review."
                    ),
                    locator=record_locator,
                )
            )
        warnings.append(
            ParseWarning(
                code="DBC_PO_REFERENCE_MISSING",
                message=(
                    "This DBC export has no explicit PO reference; no PO link was inferred "
                    "from document number or quantity alone."
                ),
                locator=record_locator,
            )
        )

    return ParseResult(
        success=bool(documents),
        file_format=FileFormat.XML,
        documents=tuple(documents),
        warnings=tuple(warnings),
        layout_signature=signature,
        reason_code=None if documents else "NO_DOCUMENTS",
    )


def parse_xml(content: bytes, hints: ParserHints) -> ParseResult:
    """Parse secure XML bytes, then apply the shared party and GST guards."""

    return guard_result(_parse_xml(content, hints), hints)


def _parse_xml(content: bytes, hints: ParserHints) -> ParseResult:
    """Parse secure XML bytes into zero or more canonical documents.

    A file that fails the strict parse is tried once more without the characters XML
    1.0 forbids (Tally's ``&#4;`` marker, stray control characters; WI-IMP1, D95); each
    removal is logged on the voucher it sat in. A file that parses is read unchanged.
    """

    result = _parse_xml_bytes(content, hints)
    if result.reason_code != "XML_PARSE_ERROR" or len(content) > hints.max_bytes:
        return result
    sanitized = sanitize(content)
    if sanitized is None:
        return result
    return _parse_xml_bytes(sanitized.content, hints, sanitized)


def _removal_warnings(
    element: etree._Element,
    doc_type: DocType,
    sanitized: Sanitized,
    voucher_order: Mapping[etree._Element, int],
    synonyms: Mapping[str, tuple[str, ...]],
) -> list[ParseWarning]:
    ordinal = voucher_order.get(element)
    if ordinal is None:
        return []
    counts = sanitized.for_voucher(ordinal)
    if not counts:
        return []
    # A removal inside a field the parser reads changed that value: a warning, not a note.
    # A tag is read only in its own scope: header fields outside the line entries, line
    # fields inside them.
    header_tags = set(alias_to_field(synonyms, _HEADER_FIELDS_BY_TYPE[doc_type]))
    line_tags = set(alias_to_field(synonyms, _LINE_FIELDS))
    line_nodes = set(_line_nodes(element))
    removed = {normalize_label(tag).replace(" ", "") for tag in counts}
    read = False
    for node in element.iter():
        tag = _tag_name(node)
        if tag not in removed:
            continue
        inside = _inside_line_node(node, line_nodes)
        if (inside and tag in line_tags) or (not inside and tag in header_tags):
            read = True
            break
    level = WarnLevel.WARNING if read else WarnLevel.INFO
    return [
        ParseWarning(
            code="XML_INVALID_CHARACTERS_REMOVED",
            level=level,
            message=removal_message(counts),
            locator=element.getroottree().getpath(element),
        )
    ]


def _parse_xml_bytes(
    content: bytes, hints: ParserHints, sanitized: Sanitized | None = None
) -> ParseResult:
    empty_signature = hashlib.sha256(b"xml:empty").hexdigest()
    if len(content) > hints.max_bytes:
        return ParseResult(
            success=False,
            file_format=FileFormat.XML,
            layout_signature=empty_signature,
            reason_code="FILE_TOO_LARGE",
            warnings=(
                ParseWarning(
                    code="FILE_TOO_LARGE", message="The XML file exceeds the parser size limit."
                ),
            ),
        )

    try:
        context = etree.iterparse(
            io.BytesIO(content),
            events=("end",),
            resolve_entities=False,
            load_dtd=False,
            no_network=True,
            recover=False,
            huge_tree=False,
            remove_comments=True,
        )
        record_elements: list[etree._Element] = []
        tag_names: set[str] = set()
        for _, element in context:
            tag = _tag_name(element)
            if tag:
                tag_names.add(tag)
            if tag in _DOCUMENT_TAGS:
                record_elements.append(element)
        root = context.root
        if root is None:
            raise ValueError("XML document has no root element")
        docinfo = root.getroottree().docinfo
        if docinfo.doctype or docinfo.internalDTD is not None:
            return ParseResult(
                success=False,
                file_format=FileFormat.XML,
                layout_signature=hashlib.sha256(b"xml:doctype").hexdigest(),
                reason_code="XML_DOCTYPE_FORBIDDEN",
                warnings=(
                    ParseWarning(
                        code="XML_DOCTYPE_FORBIDDEN",
                        message="XML documents with a DOCTYPE are not accepted.",
                    ),
                ),
            )

        direct_tags = {_tag_name(child) for child in root if isinstance(child.tag, str)}
        if (
            _tag_name(root) == "ENVELOPE"
            and "DBCFIXED" in direct_tags
            and "DBCVCHNO" in direct_tags
        ):
            return _parse_dbc_flat(root, hints, tag_names)

        # A document can itself be the root without a wrapper record element.
        if not record_elements and _tag_name(root) in _DOCUMENT_TAGS:
            record_elements.append(root)
        candidate_elements = set(record_elements)
        record_elements = [
            element
            for element in record_elements
            if not any(ancestor in candidate_elements for ancestor in element.iterancestors())
        ]
        if not record_elements:
            return ParseResult(
                success=False,
                file_format=FileFormat.XML,
                layout_signature=hashlib.sha256(b"xml:no-records").hexdigest(),
                reason_code="NO_DOCUMENTS",
                warnings=(
                    ParseWarning(
                        code="NO_DOCUMENTS",
                        message="No PO, GRN, or invoice record was found in the XML.",
                    ),
                ),
            )

        synonyms = combined_synonyms(hints.synonyms)
        aliases = alias_to_field(synonyms)
        known_tags = set(aliases) | _LINE_TAGS | _DOCUMENT_TAGS
        type_hints = [_document_type(element, hints) for element in record_elements]
        if all(doc_type is None for doc_type in type_hints):
            return ParseResult(
                success=False,
                file_format=FileFormat.XML,
                layout_signature=hashlib.sha256(b"xml:unknown-type").hexdigest(),
                reason_code="DOC_TYPE_REQUIRED",
                warnings=(
                    ParseWarning(
                        code="DOC_TYPE_REQUIRED",
                        message=(
                            "The XML document type could not be inferred; provide its upload zone."
                        ),
                    ),
                ),
            )

        raw_date_values = tuple(
            _text(node)
            for element in record_elements
            for node in element.iter()
            if aliases.get(_tag_name(node)) == "doc_date" and _text(node)
        )
        selected_profile = hints.profile or (
            "tally" if _tag_name(root) in {"ENVELOPE", "TALLYDATA"} else "generic"
        )
        documents = []
        warnings = []
        voucher_order: dict[etree._Element, int] = {}
        if sanitized is not None:
            vouchers = [node for node in root.iter() if _tag_name(node) == "VOUCHER"]
            if len(vouchers) == sanitized.voucher_count:
                voucher_order = {node: index for index, node in enumerate(vouchers)}
            unplaced = sanitized.outside_vouchers()
            if not voucher_order:
                unplaced = sum(sanitized.removed.values())
            if unplaced:
                warnings.append(
                    ParseWarning(
                        code="XML_INVALID_CHARACTERS_REMOVED",
                        level=WarnLevel.INFO,
                        message=(
                            f"Removed {unplaced} characters XML does not allow outside the "
                            "vouchers read (masters are ignored); the text around them was kept."
                        ),
                    )
                )
        for element in record_elements:
            try:
                document, document_warnings = _parse_one(
                    element,
                    hints,
                    synonyms,
                    known_tags,
                    raw_date_values,
                )
            except ValueError:
                warnings.append(
                    ParseWarning(
                        code="DOC_TYPE_REQUIRED",
                        message="The XML record could not be assigned a supported document type.",
                        locator=element.getroottree().getpath(element),
                    )
                )
                continue
            except Exception:
                warnings.append(
                    ParseWarning(
                        code="RECORD_PARSE_ERROR",
                        message="An XML record could not be converted to the canonical schema.",
                        locator=element.getroottree().getpath(element),
                    )
                )
                continue
            documents.append(document)
            warnings.extend(document_warnings)
            if sanitized is not None:
                warnings.extend(
                    _removal_warnings(
                        element,
                        document.header.doc_type,
                        sanitized,
                        voucher_order,
                        synonyms,
                    )
                )

        if not documents:
            return ParseResult(
                success=False,
                file_format=FileFormat.XML,
                layout_signature=hashlib.sha256(b"xml:no-typed-documents").hexdigest(),
                reason_code="DOC_TYPE_REQUIRED",
                warnings=tuple(warnings),
            )
        signature_payload = "|".join(
            [selected_profile, *sorted(_DOCUMENT_TAGS), *sorted(tag_names)]
        ).encode("utf-8")
        return ParseResult(
            success=True,
            file_format=FileFormat.XML,
            documents=tuple(documents),
            warnings=tuple(warnings),
            layout_signature=hashlib.sha256(signature_payload).hexdigest(),
        )
    except Exception:
        return ParseResult(
            success=False,
            file_format=FileFormat.XML,
            layout_signature=empty_signature,
            reason_code="XML_PARSE_ERROR",
            warnings=(
                ParseWarning(
                    code="XML_PARSE_ERROR", message="The XML file could not be parsed safely."
                ),
            ),
        )
