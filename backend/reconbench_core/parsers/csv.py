"""Text-only CSV parsing with synonym-based headers and document grouping."""

from __future__ import annotations

import csv
import hashlib
import io
from collections import OrderedDict
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from charset_normalizer import from_bytes

from reconbench_core.parsers.common import (
    _HEADER_FIELDS_BY_TYPE,
    alias_to_field,
    build_document,
    combined_synonyms,
    normalize_label,
)
from reconbench_core.parsers.parties import guard_result
from reconbench_core.parsers.types import ParseResult, ParserHints, ParseWarning
from reconbench_core.schema import DocType, Document, FieldOrigin, FieldSource, FileFormat

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


def _failure(code: str, message: str, signature: str) -> ParseResult:
    return ParseResult(
        success=False,
        file_format=FileFormat.CSV,
        warnings=(ParseWarning(code=code, message=message),),
        layout_signature=signature,
        reason_code=code,
    )


def _decode(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        match = from_bytes(content).best()
        encoding = match.encoding if match is not None and match.encoding else "cp1252"
        return content.decode(encoding)


def _document_type(
    hint_type: DocType | None,
    headers: list[str],
    rows: list[list[str]],
    alias_map: Mapping[str, str],
) -> DocType | None:
    if hint_type is not None:
        return hint_type
    normalized_headers = [normalize_label(header) for header in headers]
    # The document's own number column names its type ("GRN No", "Invoice No").
    # Receipts and invoices routinely carry a PO reference column, so a PO
    # label only decides the type when no receipt or invoice number is present.
    own_numbers = [name for name in normalized_headers if alias_map.get(name) == "doc_no"]
    if any(any(word in name for word in ("GRN", "MRN", "RECEIPT")) for name in own_numbers):
        return DocType.GRN
    if any("INVOICE" in name or "BILL" in name for name in own_numbers):
        return DocType.INVOICE
    if any("PURCHASE ORDER" in name or name == "PO NO" for name in normalized_headers):
        return DocType.PO
    if any(
        any(word in name for word in ("GRN", "MRN", "RECEIPT NO")) for name in normalized_headers
    ):
        return DocType.GRN
    type_index = next(
        (
            index
            for index, header in enumerate(headers)
            if alias_map.get(normalize_label(header)) == "doc_type"
        ),
        None,
    )
    if type_index is not None:
        types = {
            row[type_index].strip().casefold().replace(" ", "_")
            for row in rows
            if type_index < len(row) and row[type_index].strip()
        }
        if len(types) == 1:
            try:
                return DocType(types.pop())
            except ValueError:
                pass
    if any("INVOICE" in name or "BILL NO" in name for name in normalized_headers):
        return DocType.INVOICE
    return None


def _row_ranges(numbers: list[int]) -> str:
    ordered = sorted(set(numbers))
    if not ordered:
        return "rows:0-0"
    ranges: list[str] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(f"{start}-{previous}")
        start = previous = number
    ranges.append(f"{start}-{previous}")
    return "rows:" + ",".join(ranges)


def parse_csv(content: bytes, hints: ParserHints) -> ParseResult:
    """Parse CSV bytes, then apply the shared party and GST guards."""

    return guard_result(_parse_csv(content, hints), hints)


def _parse_csv(content: bytes, hints: ParserHints) -> ParseResult:
    """Parse CSV rows as text and group them into canonical documents."""

    empty_signature = hashlib.sha256(b"csv:empty").hexdigest()
    if len(content) > hints.max_bytes:
        return _failure(
            "FILE_TOO_LARGE", "The CSV file exceeds the parser size limit.", empty_signature
        )
    if not content:
        return _failure("EMPTY_CSV", "The CSV file is empty.", empty_signature)

    try:
        text = _decode(content)
        sample = text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text, newline=""), dialect)
        all_rows = [(reader.line_num, [cell for cell in row]) for row in reader]
        if not all_rows:
            return _failure("EMPTY_CSV", "The CSV file contains no rows.", empty_signature)

        synonyms = combined_synonyms(hints.synonyms)
        alias_map = alias_to_field(synonyms)
        custom_columns = {
            normalize_label(name): canonical
            for name, canonical in hints.column_mapping.items()
            if canonical in _HEADER_FIELDS | _LINE_FIELDS | {"doc_type"}
        }
        header_index: int | None = None
        selected_headers: list[str] = []
        selected_mapping: list[str | None] = []
        for index, (_, row) in enumerate(all_rows[:100]):
            mapped = [
                custom_columns.get(normalize_label(cell), alias_map.get(normalize_label(cell)))
                for cell in row
            ]
            if len({name for name in mapped if name is not None}) >= 3:
                header_index = index
                selected_headers = row
                selected_mapping = mapped
                break

        signature_headers = selected_headers
        if header_index is None:
            signature_headers = max(
                (row for _line, row in all_rows[:100]),
                key=lambda row: sum(bool(cell.strip()) for cell in row),
                default=[],
            )
        preliminary_signature = hashlib.sha256(
            "|".join(sorted({normalize_label(header) for header in signature_headers})).encode(
                "utf-8"
            )
        ).hexdigest()
        if header_index is None:
            return _failure(
                "CSV_MAPPING_REQUIRED",
                "No row matched at least three known column names; a column mapping is required.",
                preliminary_signature,
            )

        data_rows = all_rows[header_index + 1 :]
        if not data_rows:
            return _failure("EMPTY_CSV", "The CSV header has no data rows.", preliminary_signature)

        row_records: list[tuple[int, dict[str, str], list[str], dict[str, list[str]]]] = []
        for row_number, cells in data_rows:
            padded = cells + [""] * max(0, len(selected_mapping) - len(cells))
            mapped_values: dict[str, list[str]] = {}
            for column_index, canonical in enumerate(selected_mapping):
                if (
                    canonical is not None
                    and column_index < len(padded)
                    and padded[column_index].strip()
                ):
                    value = padded[column_index].strip()
                    candidates = mapped_values.setdefault(canonical, [])
                    if value not in candidates:
                        candidates.append(value)
            if not any(value.strip() for value in cells):
                continue
            mapped_row = {name: values[0] for name, values in mapped_values.items()}
            row_records.append((row_number, mapped_row, cells, mapped_values))

        if not row_records:
            return _failure(
                "EMPTY_CSV", "The CSV has no populated data rows.", preliminary_signature
            )

        row_maps = [record[1] for record in row_records]
        doc_type_values = {
            row["doc_type"].strip().casefold().replace(" ", "_")
            for row in row_maps
            if row.get("doc_type")
        }
        doc_type: DocType | None = hints.doc_type
        if doc_type is None and len(doc_type_values) == 1:
            try:
                doc_type = DocType(doc_type_values.pop())
            except ValueError:
                doc_type = None
        if doc_type is None:
            doc_type = _document_type(
                None, selected_headers, [record[2] for record in row_records], alias_map
            )
        if doc_type is None:
            return _failure(
                "DOC_TYPE_REQUIRED",
                "The CSV document type could not be inferred; provide its upload zone.",
                preliminary_signature,
            )

        header_aliases = alias_to_field(synonyms, _HEADER_FIELDS_BY_TYPE[doc_type])
        line_aliases = alias_to_field(synonyms, _LINE_FIELDS)
        scoped_mapping: list[str | None] = []
        for header in selected_headers:
            normalized_header = normalize_label(header)
            explicit = custom_columns.get(normalized_header)
            header_field = header_aliases.get(normalized_header)
            line_field = line_aliases.get(normalized_header)
            if explicit is not None:
                scoped_mapping.append(explicit)
            elif header_field and line_field and header_field != line_field:
                return _failure(
                    "CSV_MAPPING_REQUIRED",
                    (
                        f"The column '{header}' can identify either a document header or a "
                        "line field; confirm its mapping."
                    ),
                    preliminary_signature,
                )
            else:
                scoped_mapping.append(header_field or line_field)
        selected_mapping = scoped_mapping

        # The first pass only identifies a plausible header row and may map an
        # overlapping label to a field that is unavailable for the inferred
        # document type. Rebuild row values using the type-scoped mapping so the
        # initial guess (for example, Invoice No as a GRN cross-reference) does
        # not leak into document persistence.
        scoped_records: list[tuple[int, dict[str, str], list[str], dict[str, list[str]]]] = []
        for row_number, _mapped_row, raw_cells, _mapped_values in row_records:
            padded = raw_cells + [""] * max(0, len(selected_mapping) - len(raw_cells))
            scoped_values: dict[str, list[str]] = {}
            for column_index, canonical in enumerate(selected_mapping):
                if canonical is None or column_index >= len(padded):
                    continue
                value = padded[column_index].strip()
                if value:
                    candidates = scoped_values.setdefault(canonical, [])
                    if value not in candidates:
                        candidates.append(value)
            mapped_row = {name: values[0] for name, values in scoped_values.items()}
            scoped_records.append((row_number, mapped_row, raw_cells, scoped_values))
        row_records = scoped_records
        row_maps = [record[1] for record in row_records]

        groups: OrderedDict[
            str,
            list[tuple[int, dict[str, str], dict[str, Any], dict[str, list[str]]]],
        ] = OrderedDict()
        for row_number, mapped_row, raw_cells, mapped_values in row_records:
            document_no = mapped_row.get("doc_no", "")
            group_key = document_no.casefold() if document_no else f"missing:{row_number}"
            line_values = {
                name: value for name, value in mapped_row.items() if name in _LINE_FIELDS
            }
            extras = {
                selected_headers[index]: raw_cells[index]
                for index, canonical in enumerate(selected_mapping)
                if canonical is None and index < len(raw_cells) and raw_cells[index].strip()
            }
            groups.setdefault(group_key, []).append(
                (
                    row_number,
                    mapped_row,
                    {**line_values, "__extras__": extras},
                    mapped_values,
                )
            )

        documents: list[Document] = []
        warnings: list[ParseWarning] = []
        all_dates = tuple(row.get("doc_date", "") for row in row_maps if row.get("doc_date"))
        for records in groups.values():
            header_raw: dict[str, str] = {}
            line_raw: list[dict[str, str]] = []
            line_extras: list[dict[str, Any]] = []
            numbers_for_group: list[int] = []
            header_extras: dict[str, Any] = {}
            header_alternatives: dict[str, list[str]] = {}
            line_alternatives: list[dict[str, list[str]]] = []
            for record_index, (row_number, mapped_row, line_mapping, mapped_values) in enumerate(
                records
            ):
                numbers_for_group.append(row_number)
                for name in _HEADER_FIELDS:
                    if name not in header_raw and mapped_row.get(name):
                        header_raw[name] = mapped_row[name]
                    if record_index == 0 and len(mapped_values.get(name, [])) > 1:
                        header_alternatives[name] = mapped_values[name][1:]
                line_raw.append(
                    {name: value for name, value in line_mapping.items() if name != "__extras__"}
                )
                line_alternatives.append(
                    {
                        name: values[1:]
                        for name, values in mapped_values.items()
                        if name in _LINE_FIELDS and len(values) > 1
                    }
                )
                extras_value: Any = line_mapping.get("__extras__", {})
                line_extras.append(extras_value if isinstance(extras_value, dict) else {})
                for key, value in extras_value.items() if isinstance(extras_value, dict) else ():
                    header_extras.setdefault(key, value)

            locator = _row_ranges(numbers_for_group)
            document, document_warnings = build_document(
                doc_type=doc_type,
                header_raw=header_raw,
                line_raw=line_raw,
                header_extras=header_extras,
                line_extras=line_extras,
                header_alternatives=header_alternatives,
                line_alternatives=line_alternatives,
                locator=locator,
                source=FieldSource.RULE,
                origin=FieldOrigin.ERP_IMPORT,
                base_confidence=Decimal("0.90"),
                same_file_dates=all_dates,
                uom_aliases=hints.uom_aliases,
                charge_keywords=hints.charge_keywords,
            )
            documents.append(document)
            warnings.extend(document_warnings)

        return ParseResult(
            success=bool(documents),
            file_format=FileFormat.CSV,
            documents=tuple(documents),
            warnings=tuple(warnings),
            layout_signature=preliminary_signature,
            reason_code=None if documents else "CSV_PARSE_ERROR",
        )
    except Exception:
        return _failure(
            "CSV_PARSE_ERROR",
            "The CSV file could not be decoded or parsed safely.",
            empty_signature,
        )
