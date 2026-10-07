"""Rule-based extraction from searchable PDFs; scanned pages are never OCR-guessed."""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, cast

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

from reconbench_core.normalize import parse_date, parse_decimal
from reconbench_core.parsers.common import (
    _HEADER_FIELDS_BY_TYPE,
    alias_to_field,
    build_document,
    combined_synonyms,
    normalize_label,
)
from reconbench_core.parsers.parties import guard_result
from reconbench_core.parsers.types import PageText, ParseResult, ParserHints, ParseWarning
from reconbench_core.schema import (
    DocType,
    Document,
    FieldEvidence,
    FieldSource,
    FieldValue,
    FileFormat,
    InvoiceHeader,
    ReasonCode,
)

_BASE_CONFIDENCE = Decimal("0.80")
_OCR_CONFIDENCE_PENALTY = Decimal("0.10")
_SCANNED_TEXT_MINIMUM = 20
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
_REPEATED_HEADER_FIELDS = {"description", "quantity", "rate", "amount", "hsn_sac"}
_TOTAL_LABELS = {"TOTAL", "SUBTOTAL", "TAXABLE VALUE", "GRAND TOTAL", "NET PAYABLE"}
_UOM_SUFFIX = re.compile(r"\s*/?\s*(NOS|PCS|EA|UNIT|KG|KGS|MTR|M)\s*$", re.I)
_PO_REFERENCE = re.compile(r"\bPO\s*[/#-]\s*[A-Z0-9](?:[A-Z0-9/#-]*[A-Z0-9])\b", re.I)
_INDIAN_NUMBER_WORDS = {
    "ZERO": 0,
    "ONE": 1,
    "TWO": 2,
    "THREE": 3,
    "FOUR": 4,
    "FIVE": 5,
    "SIX": 6,
    "SEVEN": 7,
    "EIGHT": 8,
    "NINE": 9,
    "TEN": 10,
    "ELEVEN": 11,
    "TWELVE": 12,
    "THIRTEEN": 13,
    "FOURTEEN": 14,
    "FIFTEEN": 15,
    "SIXTEEN": 16,
    "SEVENTEEN": 17,
    "EIGHTEEN": 18,
    "NINETEEN": 19,
    "TWENTY": 20,
    "THIRTY": 30,
    "FORTY": 40,
    "FIFTY": 50,
    "SIXTY": 60,
    "SEVENTY": 70,
    "EIGHTY": 80,
    "NINETY": 90,
}
_PARTY_NAME_SUFFIXES = {
    "AGENCIES",
    "COMPANY",
    "COMPUTERS",
    "CORPORATION",
    "ENTERPRISES",
    "IMPEX",
    "INDUSTRIES",
    "LIMITED",
    "LLP",
    "LLC",
    "LTD",
    "PRIVATE",
    "PVT",
    "SOLUTIONS",
    "SUPPLIERS",
    "TECHNOLOGIES",
    "TRADERS",
}
_PARTY_ADDRESS_WORDS = {
    "ANDHERI",
    "BUILDING",
    "CODE",
    "EAST",
    "EMAIL",
    "ESTATE",
    "FLOOR",
    "GSTIN",
    "MUMBAI",
    "OFFICE",
    "PAWVALA",
    "PLOT",
    "ROAD",
    "STATE",
    "STREET",
    "WEST",
}


def _failure(code: str, message: str, signature: str) -> ParseResult:
    return ParseResult(
        success=False,
        file_format=FileFormat.PDF,
        warnings=(ParseWarning(code=code, message=message),),
        layout_signature=signature,
        reason_code=code,
    )


def _signature(header_names: set[str], table_headers: set[str]) -> str:
    canonical = json.dumps(
        {"headers": sorted(header_names), "table": sorted(table_headers)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _field_aliases(
    synonyms: Mapping[str, Sequence[str]],
    allowed: set[str],
    doc_type: DocType | None = None,
) -> list[tuple[tuple[str, ...], str]]:
    aliases: list[tuple[tuple[str, ...], str]] = []
    for field, labels in synonyms.items():
        if field not in allowed:
            continue
        for label in (field, *labels):
            semantic_field = _reference_alias_field(field, label, doc_type)
            if semantic_field is None:
                continue
            tokens = tuple(normalize_label(label).split())
            if tokens:
                aliases.append((tokens, semantic_field))
    return sorted(set(aliases), key=lambda item: (-len(item[0]), item[0], item[1]))


def _reference_alias_field(field: str, label: str, doc_type: DocType | None = None) -> str | None:
    """Keep references tied to explicit business labels, not generic or logistics refs."""

    normalized = normalize_label(label)
    if field == "doc_no" and doc_type is not None:
        if doc_type is DocType.INVOICE and re.search(
            r"\b(?:PO|PURCHASE ORDER|ORDER|GRN|MRN|MRR|RECEIPT)\b", normalized
        ):
            return None
        if doc_type is DocType.PO and re.search(
            r"\b(?:INVOICE|BILL|GRN|MRN|MRR|RECEIPT)\b", normalized
        ):
            return None
        if doc_type is DocType.GRN and re.search(
            r"\b(?:INVOICE|BILL|PO|PURCHASE ORDER)\b", normalized
        ):
            return None
    elif field == "po_refs":
        explicit_po = re.search(r"\b(?:P O|PURCHASE ORDER|PURCHASING DOCUMENT)\b", normalized)
        buyer_order = re.search(r"\b(?:BUYER S ORDER|CUSTOMER PO|CUST PO|YOUR PO)\b", normalized)
        against_po = re.search(r"\b(?:AGAINST PO|AS PER PO|REF PO)\b", normalized)
        if not (explicit_po or buyer_order or against_po):
            return None
    elif field == "grn_refs":
        if re.search(r"\b(?:GRN|MRN|MRR)\b", normalized):
            return "grn_refs"
        if re.search(
            r"\b(?:GOODS RECEIPT|MATERIAL RECEIPT|RECEIVING NOTE|RECEIPT NOTE)\b", normalized
        ):
            return "grn_refs"
        if "DATE" not in normalized and re.search(
            r"\b(?:DELIVERY CHALLAN|CHALLAN|DC)\b", normalized
        ):
            return "challan_no"
        if "DATE" not in normalized and re.search(
            r"\bDELIVERY NOTE\b.*\b(?:NO|NUMBER|REF|REFERENCE)\b", normalized
        ):
            return "challan_no"
        if re.search(r"\bE WAY BILL\b", normalized):
            return "e_way_bill_no"
        return None
    elif field == "supplier_invoice_no":
        if not re.search(r"\b(?:SUPPLIER|VENDOR|PARTY) INVOICE\b", normalized):
            return None
    elif field == "challan_no" and "DATE" in normalized:
        return None
    return field


def _word_tokens(words: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[int]]:
    tokens: list[str] = []
    owners: list[int] = []
    for index, word in enumerate(words):
        pieces = normalize_label(str(word.get("text", ""))).split()
        for piece in pieces:
            tokens.append(piece)
            owners.append(index)
    return tokens, owners


def _line_groups(words: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    groups: list[list[Mapping[str, Any]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        if not groups or abs(float(groups[-1][0]["top"]) - top) > 3.0:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def _bbox(words: Sequence[Mapping[str, Any]]) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
    if not words:
        return None
    return (
        Decimal(str(round(min(float(word["x0"]) for word in words), 2))),
        Decimal(str(round(min(float(word["top"]) for word in words), 2))),
        Decimal(str(round(max(float(word["x1"]) for word in words), 2))),
        Decimal(str(round(max(float(word["bottom"]) for word in words), 2))),
    )


def _evidence(page: int, snippet: str, words: Sequence[Mapping[str, Any]]) -> FieldEvidence:
    return FieldEvidence(page=page, snippet=snippet, bbox=_bbox(words))


def _match_label(
    words: Sequence[Mapping[str, Any]], aliases: Sequence[tuple[tuple[str, ...], str]]
) -> tuple[str, int, int] | None:
    tokens, owners = _word_tokens(words)
    for start in range(len(tokens)):
        for alias, field in aliases:
            stop = start + len(alias)
            if tuple(tokens[start:stop]) == alias:
                return field, owners[start], owners[stop - 1]
    return None


def _match_labels(
    words: Sequence[Mapping[str, Any]], aliases: Sequence[tuple[tuple[str, ...], str]]
) -> list[tuple[str, int, int]]:
    """Find each non-overlapping label in a row, preserving its word indexes."""

    tokens, owners = _word_tokens(words)
    found: list[tuple[str, int, int]] = []
    index = 0
    while index < len(tokens):
        match = next(
            (
                (alias, field)
                for alias, field in aliases
                if tuple(tokens[index : index + len(alias)]) == alias
            ),
            None,
        )
        if match is None:
            index += 1
            continue
        alias, field = match
        found.append((field, owners[index], owners[index + len(alias) - 1]))
        index += len(alias)
    return found


def _line_value(
    groups: Sequence[Sequence[Mapping[str, Any]]],
    group_index: int,
    start_word: int,
    end_word: int,
    aliases: Sequence[tuple[tuple[str, ...], str]],
    *,
    next_label_x: float | None = None,
) -> tuple[str, list[Mapping[str, Any]]] | None:
    current = groups[group_index]
    same_line = list(current[end_word + 1 :])
    if next_label_x is not None:
        same_line = [word for word in same_line if float(word["x0"]) < next_label_x]
    if same_line:
        first = dict(same_line[0])
        first["text"] = str(first.get("text", "")).lstrip(":-–—#")
        if first["text"]:
            same_line[0] = first
        value = " ".join(str(word.get("text", "")) for word in same_line).strip(" :-–—#")
        if value and _match_label(same_line, aliases) is None:
            return value, same_line
    if group_index + 1 >= len(groups):
        return None
    next_line = list(groups[group_index + 1])
    vertical_gap = float(next_line[0]["top"]) - float(current[-1]["bottom"])
    if vertical_gap > 36 or _match_label(next_line, aliases) is not None:
        return None
    left_bound = max(0.0, float(current[start_word]["x0"]) - 30.0)
    next_line = [
        word
        for word in next_line
        if float(word["x0"]) >= left_bound
        and (next_label_x is None or float(word["x0"]) < next_label_x - 24.0)
    ]
    if not next_line:
        return None
    value = " ".join(str(word.get("text", "")) for word in next_line).strip(" :-–—#")
    if not value:
        return None
    return value, next_line


def _extract_header_page(
    groups: Sequence[Sequence[Mapping[str, Any]]],
    aliases: Sequence[tuple[tuple[str, ...], str]],
    doc_type: DocType,
) -> list[tuple[str, str, list[Mapping[str, Any]], str]]:
    found: list[tuple[str, str, list[Mapping[str, Any]], str]] = []
    for group_index, group in enumerate(groups):
        matches = _match_labels(group, aliases)
        for match_index, (field, start_word, end_word) in enumerate(matches):
            next_label_x = (
                float(group[matches[match_index + 1][1]]["x0"])
                if match_index + 1 < len(matches)
                else None
            )
            reading = _line_value(
                groups,
                group_index,
                start_word,
                end_word,
                aliases,
                next_label_x=next_label_x,
            )
            if reading is None:
                continue
            value, value_words = reading
            if not _valid_text_header_reading(field, value):
                continue
            label = normalize_label(" ".join(str(word.get("text", "")) for word in group))
            if _header_reading_priority(doc_type, field, label) <= 0:
                continue
            found.append((field, value, value_words, label))
    return found


def _extract_header_text_lines(
    text: str,
    aliases: Sequence[tuple[tuple[str, ...], str]],
    doc_type: DocType,
) -> list[tuple[str, str, str]]:
    """Recover labeled OCR readings when OCR word geometry is out of order."""

    found: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        token_spans = list(re.finditer(r"[A-Z0-9]+", line, flags=re.IGNORECASE))
        tokens = tuple(match.group(0).upper() for match in token_spans)
        matches: list[tuple[int, int, str, str]] = []
        index = 0
        while index < len(tokens):
            alias_match = next(
                (
                    (alias, field)
                    for alias, field in aliases
                    if tokens[index : index + len(alias)] == alias
                ),
                None,
            )
            if alias_match is None:
                index += 1
                continue
            alias, field = alias_match
            start = token_spans[index].start()
            end = token_spans[index + len(alias) - 1].end()
            label = normalize_label(line[max(0, start - 36) : end])
            matches.append((start, end, field, label))
            index += len(alias)

        for match_index, (_start, end, field, label) in enumerate(matches):
            next_start = (
                matches[match_index + 1][0] if match_index + 1 < len(matches) else len(line)
            )
            value = line[end:next_start].strip(" \t.:#–—-")
            if (
                value
                and _header_reading_priority(doc_type, field, label) > 0
                and _valid_text_header_reading(field, value)
            ):
                found.append((field, value, label))
    return found


def _header_reading_priority(doc_type: DocType, field: str, label: str) -> int:
    """Prefer labels for the selected document when PDFs bundle other records."""

    normalized = normalize_label(label)
    if field == "doc_date":
        if any(term in normalized for term in ("HANDWRITTEN", "SIGNATURE", "DECLARATION")):
            return 0
        if doc_type is DocType.INVOICE:
            if "INVOICE" in normalized and "DATE" in normalized:
                return 50
            if "INVOICE" in normalized and "DATED" in normalized:
                return 50
            if normalized == "DATED":
                return 45
            if normalized in {"DOC DATE", "DOCUMENT DATE", "BILL DATE"}:
                return 40
            if "ORDER" in normalized or "GRN" in normalized or "RECEIPT" in normalized:
                return 0
        elif doc_type is DocType.PO:
            if "ORDER" in normalized and "DATE" in normalized:
                return 50
            if normalized in {"DOC DATE", "DOCUMENT DATE", "PO DATE"}:
                return 40
            if "INVOICE" in normalized or "GRN" in normalized or "RECEIPT" in normalized:
                return 0
        else:
            if "GRN" in normalized or "RECEIPT" in normalized:
                return 50
            if normalized in {"DOC DATE", "DOCUMENT DATE"}:
                return 40
            if "INVOICE" in normalized or "ORDER" in normalized:
                return 0
        return 20 if normalized == "DATE" else 10
    if field == "doc_no":
        type_terms = {
            DocType.INVOICE: {"INVOICE", "BILL"},
            DocType.PO: {"PO", "PURCHASE ORDER", "ORDER"},
            DocType.GRN: {"GRN", "MRN", "RECEIPT"},
        }[doc_type]
        if any(term in normalized for term in type_terms):
            return 50
        if "VOUCHER" in normalized:
            return 30
        return 20
    if field == "grand_total":
        if "HSN" in normalized and any(
            tax_label in normalized for tax_label in ("CGST", "SGST", "IGST")
        ):
            return 0
        if "INVOICE" in normalized or "GRAND" in normalized or "NET PAYABLE" in normalized:
            return 50
        if normalized in {"TOTAL VALUE", "TOTAL AMOUNT", "AMOUNT PAYABLE"}:
            return 40
        if normalized == "TOTAL":
            return 10
    return 20


def _valid_text_header_reading(field: str, value: str) -> bool:
    """Reject OCR label/value spillover before it can replace a better reading."""

    if field in {"doc_date", "delivery_date"}:
        try:
            parse_date(value)
        except ValueError:
            return False
    elif field in {"taxable_total", "cgst", "sgst", "igst", "round_off", "grand_total"}:
        try:
            parse_decimal(value)
        except ValueError:
            return False
    elif field in {"po_refs", "grn_refs", "challan_no", "e_way_bill_no"}:
        # A punctuation artifact or address spillover must never become a document link.
        parts = re.findall(r"[A-Z0-9]+", value.upper())
        if not parts or len(value) > 128:
            return False
        if not any(any(character.isdigit() for character in part) for part in parts):
            compact_code = re.fullmatch(r"[A-Z]{2,8}[-/][A-Z0-9]{2,24}", value.upper())
            if not compact_code:
                return False
            if any(part in _PARTY_ADDRESS_WORDS for part in parts):
                return False
    return bool(value.strip())


def _parse_indian_amount_words(value: str) -> Decimal | None:
    """Parse a whole-rupee amount written in common Indian English words."""

    tokens = re.findall(r"[A-Z]+", value.upper())
    ignored = {"AND", "INDIAN", "ONLY", "RUPEE", "RUPEES"}
    current = 0
    total = 0
    found_number = False
    for token in tokens:
        if token in ignored:
            continue
        number = _INDIAN_NUMBER_WORDS.get(token)
        if number is not None:
            current += number
            found_number = True
        elif token == "HUNDRED":
            current = max(current, 1) * 100
            found_number = True
        elif token in {"THOUSAND", "THOUSANDS"}:
            total += max(current, 1) * 1_000
            current = 0
            found_number = True
        elif token in {"LAKH", "LAKHS", "LAC", "LACS"}:
            total += max(current, 1) * 100_000
            current = 0
            found_number = True
        elif token in {"CRORE", "CRORES", "CR"}:
            total += max(current, 1) * 10_000_000
            current = 0
            found_number = True
        else:
            return None
    return Decimal(total + current) if found_number else None


def _amount_chargeable_in_words(
    groups: Sequence[Sequence[Mapping[str, Any]]], page_number: int
) -> tuple[Decimal, FieldEvidence] | None:
    """Read the invoice's payable total from its specifically labeled words field."""

    for group_index, group in enumerate(groups):
        label_text = " ".join(str(word.get("text", "")) for word in group)
        normalized = normalize_label(label_text)
        if "TAX AMOUNT" in normalized or "WORDS" not in normalized:
            continue
        if not any(
            phrase in normalized
            for phrase in ("AMOUNT CHARGEABLE", "TOTAL AMOUNT", "INVOICE VALUE")
        ):
            continue

        value_words: Sequence[Mapping[str, Any]] = ()
        same_line_value = re.sub(
            r"^.*?(?:AMOUNT CHARGEABLE|TOTAL AMOUNT|INVOICE VALUE)\s*(?:IN WORDS)?\s*:?",
            "",
            label_text,
            flags=re.IGNORECASE,
        ).strip()
        amount = _parse_indian_amount_words(same_line_value)
        if amount is not None:
            value_words = group
        elif group_index + 1 < len(groups):
            next_group = groups[group_index + 1]
            vertical_gap = float(next_group[0]["top"]) - float(group[-1]["bottom"])
            if vertical_gap <= 30:
                value_text = " ".join(str(word.get("text", "")) for word in next_group)
                amount = _parse_indian_amount_words(value_text)
                if amount is not None:
                    value_words = next_group
        if amount is not None and value_words:
            snippet = " ".join(str(word.get("text", "")) for word in value_words).strip()
            return amount, _evidence(page_number, snippet, value_words)
    return None


def _invoice_seller_name(
    groups: Sequence[Sequence[Mapping[str, Any]]], vendor_gstin: str | None
) -> tuple[str, list[Mapping[str, Any]]] | None:
    """Infer an unlabeled seller heading only when it aligns with seller GSTIN."""

    if not vendor_gstin:
        return None
    wanted_gstin = re.sub(r"[^A-Z0-9]", "", vendor_gstin.upper())
    for gstin_index, group in enumerate(groups):
        group_text = " ".join(str(word.get("text", "")) for word in group)
        if "GSTIN" not in normalize_label(group_text):
            continue
        compact_text = re.sub(r"[^A-Z0-9]", "", group_text.upper())
        if wanted_gstin not in compact_text:
            continue
        gstin_label = next(
            (word for word in group if "GSTIN" in normalize_label(str(word.get("text", "")))),
            group[0],
        )
        target_x = float(gstin_label["x0"])
        candidates: list[tuple[int, int, str, list[Mapping[str, Any]]]] = []
        for candidate_index in range(max(0, gstin_index - 9), gstin_index):
            candidate_group = groups[candidate_index]
            vertical_distance = float(gstin_label["top"]) - float(candidate_group[0]["top"])
            if vertical_distance > 65:
                continue
            column_words = [
                word
                for word in candidate_group
                if target_x - 18 <= float(word["x0"]) <= target_x + 225
            ]
            if not column_words or abs(float(column_words[0]["x0"]) - target_x) > 24:
                continue
            text = " ".join(str(word.get("text", "")) for word in column_words).strip(" :,-")
            normalized = normalize_label(text)
            tokens = normalized.split()
            if len(tokens) < 2 or any(token.isdigit() for token in tokens):
                continue
            if any(token in _PARTY_ADDRESS_WORDS for token in tokens):
                continue
            if any(
                marker in normalized
                for marker in (
                    "BUYER",
                    "BILL TO",
                    "CONSIGNEE",
                    "CUSTOMER",
                    "INVOICE",
                    "SHIP TO",
                    "SOLD TO",
                    "GSTIN",
                )
            ):
                continue
            letters = [character for character in text if character.isalpha()]
            uppercase_ratio = (
                sum(character.isupper() for character in letters) / len(letters) if letters else 0.0
            )
            has_company_suffix = bool(set(tokens) & _PARTY_NAME_SUFFIXES)
            if uppercase_ratio < 0.78 and not has_company_suffix:
                continue
            # Prefer a legal/business-style heading; if equal, choose the nearest
            # qualifying line to the GSTIN block.
            score = int(has_company_suffix) * 100 + int(uppercase_ratio * 10)
            candidates.append((score, -int(vertical_distance * 10), text, column_words))
        if candidates:
            _score, _distance, name, words = max(candidates, key=lambda item: (item[0], item[1]))
            return name, words
    return None


def _invoice_buyer_gstin(
    groups: Sequence[Sequence[Mapping[str, Any]]], page_number: int
) -> tuple[str, FieldEvidence] | None:
    """Find GSTIN inside a clearly labeled buyer/bill-to identity block."""

    candidates: list[tuple[int, int, str, FieldEvidence]] = []
    gstin_pattern = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b", re.I)
    for marker_index, marker_group in enumerate(groups):
        marker_text = normalize_label(" ".join(str(word.get("text", "")) for word in marker_group))
        if "ORDER" in marker_text:
            continue
        is_bill_to = "BILL TO" in marker_text or ("BUYER" in marker_text and "BILL" in marker_text)
        is_consignee = (
            "CONSIGNEE" in marker_text or "SHIP TO" in marker_text or "SOLD TO" in marker_text
        )
        if not is_bill_to and not is_consignee:
            continue
        for gstin_index in range(marker_index + 1, min(marker_index + 12, len(groups))):
            group = groups[gstin_index]
            vertical_gap = float(group[0]["top"]) - float(marker_group[0]["top"])
            if vertical_gap > 90:
                break
            normalized_group = normalize_label(
                " ".join(str(word.get("text", "")) for word in group)
            )
            if any(
                term in normalized_group for term in ("CONSIGNEE", "BILL TO", "SHIP TO", "SOLD TO")
            ):
                break
            if "GSTIN" not in normalized_group:
                continue
            raw_group = " ".join(str(word.get("text", "")) for word in group)
            match = gstin_pattern.search(raw_group)
            if match is None:
                continue
            evidence_words = [
                word
                for word in group
                if match.group(0).casefold() in str(word.get("text", "")).casefold()
            ]
            if not evidence_words:
                evidence_words = list(group)
            priority = 2 if is_bill_to else 1
            candidates.append(
                (
                    priority,
                    -gstin_index,
                    match.group(0).upper(),
                    _evidence(page_number, match.group(0).upper(), evidence_words),
                )
            )
            break
    if not candidates:
        return None
    _priority, _index, gstin, evidence = max(candidates, key=lambda item: (item[0], item[1]))
    return gstin, evidence


def _invoice_po_references(
    groups: Sequence[Sequence[Mapping[str, Any]]], page_number: int
) -> list[tuple[str, FieldEvidence]]:
    references: list[tuple[str, FieldEvidence]] = []
    for group in groups:
        text = " ".join(str(word.get("text", "")) for word in group)
        words_by_reference = [
            match.group(0).replace(" ", "") for match in _PO_REFERENCE.finditer(text)
        ]
        if not words_by_reference:
            continue
        snippet = " ".join(dict.fromkeys(words_by_reference))
        references.append((snippet, _evidence(page_number, snippet, group)))
    return references


def _cell_header(row: Sequence[str | None], aliases: Mapping[str, str]) -> list[str | None] | None:
    mapped = [aliases.get(normalize_label(cell or "")) for cell in row]
    recognized = {field for field in mapped if field in _REPEATED_HEADER_FIELDS}
    return mapped if len(recognized) >= 3 else None


def _is_total_row(values: Mapping[str, str]) -> bool:
    description = normalize_label(values.get("description", ""))
    return description in _TOTAL_LABELS or description.startswith("GRAND TOTAL ")


def _extract_tables(
    page: Any,
    page_number: int,
    line_aliases: Mapping[str, str],
    line_evidence: list[dict[str, FieldEvidence]],
) -> tuple[list[dict[str, str]], set[str]]:
    try:
        tables = page.extract_tables()
    except Exception:
        tables = []
    if not tables:
        try:
            tables = page.extract_tables(
                {"vertical_strategy": "text", "horizontal_strategy": "text"}
            )
        except Exception:
            tables = []
    rows_out: list[dict[str, str]] = []
    header_names: set[str] = set()
    for table in tables:
        columns: list[str | None] | None = None
        previous_description_index: int | None = None
        for row in table:
            cells = [str(value).strip() if value is not None else "" for value in row]
            detected = _cell_header(cells, line_aliases)
            if detected is not None:
                columns = detected
                header_names.update(field for field in detected if field is not None)
                previous_description_index = None
                continue
            if columns is None:
                continue
            raw: dict[str, str] = {}
            for index, field in enumerate(columns):
                if field is not None and index < len(cells) and cells[index]:
                    raw.setdefault(field, cells[index].replace("\n", " ").strip())
            if not raw or _is_total_row(raw):
                continue
            has_numeric_or_code = any(
                raw.get(name) for name in ("quantity", "rate", "amount", "item_code", "hsn_sac")
            )
            if not has_numeric_or_code and raw.get("description"):
                if previous_description_index is not None:
                    prior = rows_out[previous_description_index]
                    prior["description"] = f"{prior.get('description', '')}\n{raw['description']}"
                continue
            if not has_numeric_or_code:
                continue
            raw["line_no"] = raw.get("line_no") or str(len(rows_out) + 1)
            current_index = len(rows_out)
            rows_out.append(raw)
            evidence_by_field: dict[str, FieldEvidence] = {}
            for field, snippet in raw.items():
                evidence_by_field[field] = FieldEvidence(
                    page=page_number,
                    snippet=snippet,
                    # pdfplumber's table cells do not expose a stable cell box through this API.
                    # Word-level lookup below supplies a box when the cell text is on the page.
                )
            line_evidence.append(evidence_by_field)
            if raw.get("description"):
                previous_description_index = current_index
    return rows_out, header_names


def _line_rows_quality(rows: Sequence[Mapping[str, str]]) -> tuple[int, int, int, int]:
    """Rank alternative PDF table reads by complete, identifying line coverage."""

    complete_identified = 0
    identified = 0
    with_hsn = 0
    numeric = 0
    for row in rows:
        has_identity = bool(row.get("description") or row.get("item_code"))
        has_quantity = bool(row.get("quantity") or row.get("received_qty"))
        has_value = bool(row.get("rate") or row.get("amount"))
        complete_identified += int(has_identity and has_quantity and has_value)
        identified += int(has_identity)
        with_hsn += int(bool(row.get("hsn_sac")))
        numeric += int(has_quantity) + int(has_value)
    return complete_identified, identified, with_hsn, numeric


def _matched_line_labels(
    words: Sequence[Mapping[str, Any]],
    aliases: Sequence[tuple[tuple[str, ...], str]],
) -> list[tuple[str, list[Mapping[str, Any]]]]:
    tokens, owners = _word_tokens(words)
    matches: list[tuple[str, list[Mapping[str, Any]]]] = []
    index = 0
    while index < len(tokens):
        matched = next(
            (
                (alias, field)
                for alias, field in aliases
                if tuple(tokens[index : index + len(alias)]) == alias
            ),
            None,
        )
        if matched is None:
            index += 1
            continue
        alias, field = matched
        first_word = owners[index]
        last_word = owners[index + len(alias) - 1]
        matches.append((field, list(words[first_word : last_word + 1])))
        index += len(alias)
    return matches


def _text_table_headers(
    groups: Sequence[Sequence[Mapping[str, Any]]],
    aliases: Sequence[tuple[tuple[str, ...], str]],
    numeric_fields: set[str],
) -> dict[int, tuple[int, list[tuple[float, str, list[Mapping[str, Any]]]]]]:
    """Find table-header bands, including labels printed on adjacent baselines."""

    candidates: dict[int, tuple[int, list[tuple[float, str, list[Mapping[str, Any]]]]]] = {}
    for start in range(len(groups)):
        collected: list[tuple[str, list[Mapping[str, Any]]]] = []
        first_top = float(groups[start][0]["top"])
        last_top = first_top
        for end in range(start, min(start + 3, len(groups))):
            top = float(groups[end][0]["top"])
            if end > start and (top - first_top > 12.0 or top - last_top > 8.0):
                break
            matches = _matched_line_labels(groups[end], aliases)
            if not matches:
                break
            collected.extend(matches)
            last_top = top
            fields = {field for field, _label_words in collected}
            if "description" not in fields or len(fields & numeric_fields) < 2:
                continue

            columns: list[tuple[float, str, list[Mapping[str, Any]]]] = []
            seen_fields: set[str] = set()
            for field, label_words in collected:
                if field in seen_fields:
                    continue
                seen_fields.add(field)
                x0, _y0, x1, _y1 = _bbox(label_words) or (Decimal(0),) * 4
                columns.append(((float(x0) + float(x1)) / 2, field, label_words))
            columns.sort(key=lambda item: item[0])
            if len(columns) >= 3:
                candidates[start] = (end, columns)
                break
    return candidates


def _valid_numeric_cell(field: str, value: str) -> bool:
    text = value.strip()
    if field == "tax_rate" and text.endswith("%"):
        text = text[:-1].strip()
    text = re.sub(r"\s*/?\s*(?:NOS|PCS|EA|UNIT|KG|KGS|MTR|M)\s*$", "", text, flags=re.I)
    try:
        parse_decimal(text)
    except ValueError:
        return False
    return True


def _extract_text_rows(
    words: Sequence[Mapping[str, Any]],
    page_number: int,
    aliases: Sequence[tuple[tuple[str, ...], str]],
    doc_type: DocType,
) -> tuple[list[dict[str, str]], list[dict[str, FieldEvidence]], set[str]]:
    """Read unruled PDF item tables from repeated text-column headings.

    This fallback uses a row's recognized header labels to establish column
    positions. It does not infer a value from invoice-specific text or totals.
    """

    groups = _line_groups(words)
    rows_out: list[dict[str, str]] = []
    evidence_out: list[dict[str, FieldEvidence]] = []
    header_names: set[str] = set()
    numeric_fields = {
        "quantity",
        "received_qty",
        "accepted_qty",
        "rejected_qty",
        "rate",
        "discount",
        "amount",
        "tax_rate",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
    }
    headers = _text_table_headers(groups, aliases, numeric_fields)
    previous_row_bottom: float | None = None
    group_index = 0
    while group_index < len(groups):
        header = headers.get(group_index)
        if header is None:
            group_index += 1
            continue
        header_end, columns = header

        header_names.update(field for _center, field, _words in columns)
        boundaries = [
            (columns[index][0] + columns[index + 1][0]) / 2 for index in range(len(columns) - 1)
        ]
        label_words = [word for _center, _field, found_words in columns for word in found_words]
        left_edge = min(float(word["x0"]) for word in label_words)
        right_edge = max(float(word["x1"]) for word in label_words)
        description_index = next(
            (
                index
                for index, (_center, field, _label) in enumerate(columns)
                if field == "description"
            ),
            None,
        )
        group_index = header_end + 1
        while group_index < len(groups):
            if group_index in headers:
                break
            row_words = groups[group_index]

            cells: list[list[Mapping[str, Any]]] = [[] for _ in columns]
            for word in row_words:
                center = (float(word["x0"]) + float(word["x1"])) / 2
                if center < left_edge - 12 or center > right_edge + 12:
                    continue
                column_index = 0
                while column_index < len(boundaries) and center >= boundaries[column_index]:
                    column_index += 1
                cells[column_index].append(word)

            # Invoice serial numbers are often printed left of the Description
            # heading, while the description itself starts under the serial
            # column. Recover that text without letting the row number become
            # part of the product name.
            if description_index == 0 and boundaries:
                overflow = [
                    word
                    for word in row_words
                    if (float(word["x0"]) + float(word["x1"])) / 2 < left_edge - 12
                    and (float(word["x0"]) + float(word["x1"])) / 2 < boundaries[0]
                ]
                if len(overflow) > 1:
                    first = overflow[0]
                    second = overflow[1]
                    gap = float(second["x0"]) - float(first["x1"])
                    if str(first.get("text", "")).strip(".#").isdigit() and gap >= 8:
                        overflow = overflow[1:]
                cells[description_index] = [*overflow, *cells[description_index]]

            raw: dict[str, str] = {}
            evidence_by_field: dict[str, FieldEvidence] = {}
            for cell_index, (_center, field, _label_words) in enumerate(columns):
                cell_words = cells[cell_index]
                if not cell_words:
                    continue
                snippet = " ".join(str(word.get("text", "")) for word in cell_words).strip()
                if not snippet:
                    continue
                if field in numeric_fields:
                    suffix = _UOM_SUFFIX.search(snippet)
                    if suffix is not None:
                        unit = suffix.group(1).upper()
                        snippet = snippet[: suffix.start()].strip()
                        raw.setdefault("uom", unit)
                raw[field] = snippet
                evidence_by_field[field] = FieldEvidence(
                    page=page_number,
                    snippet=snippet,
                    bbox=_bbox(cell_words),
                )

            if _is_total_row(raw):
                break
            if raw.get("description") and not any(raw.get(field) for field in numeric_fields):
                previous_description_index = len(rows_out) - 1
                row_top = float(row_words[0]["top"])
                if (
                    previous_description_index >= 0
                    and previous_row_bottom is not None
                    and row_top >= previous_row_bottom
                    and row_top - previous_row_bottom <= 36
                    and len(raw["description"]) <= 160
                ):
                    prior = rows_out[previous_description_index]
                    code = raw["description"].strip()
                    if (
                        "item_code" not in prior
                        and re.fullmatch(r"[A-Z0-9][A-Z0-9./_-]{4,23}", code, re.IGNORECASE)
                        and any(character.isdigit() for character in code)
                    ):
                        prior["item_code"] = code
                        evidence_out[previous_description_index]["item_code"] = FieldEvidence(
                            page=page_number,
                            snippet=code,
                            bbox=_bbox(row_words),
                        )
                    else:
                        prior["description"] = f"{prior['description']} {code}"
                        evidence_out[previous_description_index]["description"] = FieldEvidence(
                            page=page_number,
                            snippet=prior["description"],
                        )
                group_index += 1
                continue

            valid_numeric = {
                field
                for field in numeric_fields
                if raw.get(field) and _valid_numeric_cell(field, raw[field])
            }
            if doc_type is DocType.GRN:
                has_quantity = bool(valid_numeric & {"quantity", "received_qty", "accepted_qty"})
                has_value = True
            else:
                has_quantity = "quantity" in valid_numeric
                has_value = bool(valid_numeric & {"rate", "amount"})
            if raw.get("description") and has_quantity and has_value:
                raw["line_no"] = raw.get("line_no") or str(len(rows_out) + 1)
                rows_out.append(raw)
                evidence_by_field.setdefault(
                    "line_no",
                    FieldEvidence(page=page_number, snippet=raw["line_no"]),
                )
                evidence_out.append(evidence_by_field)
                previous_row_bottom = max(float(word["bottom"]) for word in row_words)
            group_index += 1
    return rows_out, evidence_out, header_names


def _find_word_bbox(
    words: Sequence[Mapping[str, Any]], snippet: str
) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
    wanted = normalize_label(snippet).split()
    if not wanted:
        return None
    tokens, owners = _word_tokens(words)
    for start in range(0, max(0, len(tokens) - len(wanted) + 1)):
        stop = start + len(wanted)
        if tokens[start:stop] == wanted:
            return _bbox(words[owners[start] : owners[stop - 1] + 1])
    return None


def _attach_evidence(
    document: Document,
    header_evidence: Mapping[str, FieldEvidence],
    line_evidence: Sequence[Mapping[str, FieldEvidence]],
    ocr_pages: frozenset[int],
) -> Document:
    def update_field(field: FieldValue[Any], evidence: FieldEvidence | None) -> FieldValue[Any]:
        updates: dict[str, Any] = {}
        if evidence is not None:
            updates["evidence"] = evidence
            if evidence.page in ocr_pages:
                updates["confidence"] = max(
                    Decimal("0"), field.confidence - _OCR_CONFIDENCE_PENALTY
                )
        return field.model_copy(update=updates) if updates else field

    header_updates: dict[str, Any] = {}
    for name in type(document.header).model_fields:
        field = getattr(document.header, name)
        evidence = header_evidence.get(name)
        if isinstance(field, FieldValue):
            updated = update_field(field, evidence)
            if updated is not field:
                header_updates[name] = updated
    header = document.header.model_copy(update=header_updates)
    lines = []
    for index, line in enumerate(document.lines):
        values: dict[str, Any] = {}
        evidence_by_field = line_evidence[index] if index < len(line_evidence) else {}
        for name in type(line).model_fields:
            field = getattr(line, name)
            evidence = evidence_by_field.get(name)
            if isinstance(field, FieldValue):
                updated = update_field(field, evidence)
                if updated is not field:
                    values[name] = updated
        lines.append(line.model_copy(update=values))
    return document.model_copy(update={"header": header, "lines": tuple(lines)})


def _shell_document(doc_type: DocType, *, file_hash: str, page_count: int) -> Document:
    document, _warnings = build_document(
        doc_type=doc_type,
        header_raw={},
        line_raw=(),
        header_extras={},
        line_extras=(),
        locator=f"pdf:sha256:{file_hash}:pages:{page_count}",
        source=FieldSource.RULE,
        base_confidence=_BASE_CONFIDENCE,
    )
    return document.model_copy(
        update={
            "natural_key": file_hash,
            "needs_verification": True,
            "reason_codes": (ReasonCode.NO_TEXT_LAYER,),
        }
    )


def parse_pdf(
    content: bytes,
    hints: ParserHints,
    *,
    ocr_pages: frozenset[int] = frozenset(),
) -> ParseResult:
    """Extract a conservative canonical document from searchable PDF text."""

    return guard_result(_parse_pdf(content, hints, ocr_pages=ocr_pages), hints)


def _parse_pdf(
    content: bytes,
    hints: ParserHints,
    *,
    ocr_pages: frozenset[int],
) -> ParseResult:

    empty_signature = hashlib.sha256(b"pdf:empty").hexdigest()
    if len(content) > hints.max_bytes:
        return _failure("FILE_TOO_LARGE", "The PDF exceeds the parser byte limit.", empty_signature)
    if not content.startswith(b"%PDF-"):
        return _failure(
            "INVALID_PDF", "The file does not have a valid PDF header.", empty_signature
        )
    if hints.doc_type is None:
        return _failure(
            "DOC_TYPE_REQUIRED",
            "Select the PO, GRN, or invoice zone before parsing a PDF.",
            empty_signature,
        )

    synonyms = combined_synonyms(hints.synonyms)
    header_aliases = _field_aliases(
        synonyms, _HEADER_FIELDS_BY_TYPE[hints.doc_type], hints.doc_type
    )
    line_alias_map = alias_to_field(synonyms, _LINE_FIELDS)
    line_aliases = _field_aliases(synonyms, _LINE_FIELDS)
    line_evidence: list[dict[str, FieldEvidence]] = []
    header_raw: dict[str, str] = {}
    header_evidence: dict[str, FieldEvidence] = {}
    header_priorities: dict[str, int] = {}
    header_alternatives: dict[str, list[str]] = {}
    table_header_names: set[str] = set()
    header_names: set[str] = set()
    page_texts: list[PageText] = []
    all_words: dict[int, list[Mapping[str, Any]]] = {}
    raw_line_rows: list[dict[str, str]] = []
    warnings: list[ParseWarning] = []
    inferred_vendor_name = False
    inferred_po_reference = False
    amount_words_conflict = False
    amount_words_uncrosschecked = False

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            if len(pdf.pages) == 0:
                return _failure("EMPTY_PDF", "The PDF contains no pages.", empty_signature)
            if len(pdf.pages) > hints.max_pages:
                return _failure(
                    "PDF_PAGE_LIMIT", "The PDF exceeds the configured page limit.", empty_signature
                )
            for page_number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                visible_chars = sum(character.isalnum() for character in text)
                scanned = visible_chars < _SCANNED_TEXT_MINIMUM
                page_texts.append(
                    PageText(
                        page=page_number,
                        content=text,
                        scanned=scanned,
                        is_ocr=page_number in ocr_pages,
                    )
                )
                words = cast(list[Mapping[str, Any]], page.extract_words() or [])
                all_words[page_number] = words
                if scanned:
                    continue
                groups = _line_groups(words)
                for field, value, value_words, label in _extract_header_page(
                    groups, header_aliases, hints.doc_type
                ):
                    header_names.add(field)
                    snippet = value.strip()
                    priority = _header_reading_priority(hints.doc_type, field, label)
                    if field not in header_raw or priority > header_priorities[field]:
                        if field in header_raw and header_raw[field] != snippet:
                            header_alternatives.setdefault(field, []).append(header_raw[field])
                        header_raw[field] = snippet
                        header_evidence[field] = _evidence(page_number, snippet, value_words)
                        header_priorities[field] = priority
                    elif snippet != header_raw[field]:
                        alternatives = header_alternatives.setdefault(field, [])
                        if snippet not in alternatives:
                            alternatives.append(snippet)
                if page_number in ocr_pages:
                    for field, value, label in _extract_header_text_lines(
                        text, header_aliases, hints.doc_type
                    ):
                        if not _valid_text_header_reading(field, value):
                            continue
                        header_names.add(field)
                        snippet = value.strip()
                        priority = _header_reading_priority(hints.doc_type, field, label) + 100
                        if field not in header_raw or priority > header_priorities[field]:
                            if field in header_raw and header_raw[field] != snippet:
                                header_alternatives.setdefault(field, []).append(header_raw[field])
                            header_raw[field] = snippet
                            header_evidence[field] = FieldEvidence(
                                page=page_number,
                                snippet=snippet,
                                bbox=_find_word_bbox(words, snippet),
                            )
                            header_priorities[field] = priority
                        elif snippet != header_raw[field]:
                            alternatives = header_alternatives.setdefault(field, [])
                            if snippet not in alternatives:
                                alternatives.append(snippet)
                if hints.doc_type is DocType.INVOICE:
                    if not header_raw.get("vendor_name"):
                        inferred_name = _invoice_seller_name(groups, header_raw.get("vendor_gstin"))
                        if inferred_name is not None:
                            name, name_words = inferred_name
                            header_raw["vendor_name"] = name
                            header_evidence["vendor_name"] = _evidence(
                                page_number, name, name_words
                            )
                            header_priorities["vendor_name"] = 1
                            header_names.add("vendor_name")
                            inferred_vendor_name = True

                    if not header_raw.get("buyer_gstin"):
                        buyer_gstin = _invoice_buyer_gstin(groups, page_number)
                        if buyer_gstin is not None:
                            value, evidence = buyer_gstin
                            header_raw["buyer_gstin"] = value
                            header_evidence["buyer_gstin"] = evidence
                            header_names.add("buyer_gstin")

                    if not header_raw.get("po_refs"):
                        po_references = _invoice_po_references(groups, page_number)
                        if po_references:
                            unique_references = tuple(
                                dict.fromkeys(
                                    reference for reference, _evidence_value in po_references
                                )
                            )
                            header_raw["po_refs"] = "; ".join(unique_references)
                            header_evidence["po_refs"] = po_references[0][1]
                            header_names.add("po_refs")
                            inferred_po_reference = True

                    if not header_raw.get("grand_total"):
                        amount_words = _amount_chargeable_in_words(groups, page_number)
                        if amount_words is not None:
                            word_total, word_evidence = amount_words
                            arithmetic_fields = (
                                "cgst",
                                "sgst",
                                "igst",
                                "round_off",
                            )
                            known_taxes = [
                                parse_decimal(header_raw[field])
                                for field in arithmetic_fields
                                if header_raw.get(field)
                            ]
                            taxable = (
                                parse_decimal(header_raw["taxable_total"])
                                if header_raw.get("taxable_total")
                                else None
                            )
                            if taxable is not None and known_taxes:
                                expected_total = taxable + sum(known_taxes, Decimal("0"))
                                if abs(expected_total - word_total) <= Decimal("1"):
                                    header_raw["grand_total"] = format(word_total, "f")
                                    header_evidence["grand_total"] = word_evidence
                                    header_names.add("grand_total")
                                else:
                                    amount_words_conflict = True
                            else:
                                header_raw["grand_total"] = format(word_total, "f")
                                header_evidence["grand_total"] = word_evidence
                                header_names.add("grand_total")
                                amount_words_uncrosschecked = True
                table_rows, found_headers = _extract_tables(
                    page, page_number, line_alias_map, line_evidence
                )
                text_rows, text_evidence, text_headers = _extract_text_rows(
                    words, page_number, line_aliases, hints.doc_type
                )
                if _line_rows_quality(text_rows) > _line_rows_quality(table_rows):
                    table_rows = text_rows
                    line_evidence.extend(text_evidence)
                    found_headers.update(text_headers)
                raw_line_rows.extend(table_rows)
                table_header_names.update(found_headers)
    except PdfminerException as exc:
        if any(isinstance(item, PDFPasswordIncorrect) for item in exc.args):
            return _failure(
                "ENCRYPTED_PDF", "Password-protected PDFs are not supported.", empty_signature
            )
        return _failure("INVALID_PDF", "The PDF could not be read safely.", empty_signature)
    except Exception:
        return _failure("INVALID_PDF", "The PDF could not be read safely.", empty_signature)

    signature = _signature(header_names, table_header_names)
    if not page_texts:
        return _failure("EMPTY_PDF", "The PDF contains no readable pages.", signature)
    file_hash = hashlib.sha256(content).hexdigest()
    scanned_pages = [page.page for page in page_texts if page.scanned]
    if all(page.scanned for page in page_texts):
        warnings.append(
            ParseWarning(
                code="NO_TEXT_LAYER",
                message=(
                    "No readable text was extracted. The original PDF is preserved as a shell "
                    "document for human review."
                ),
                locator="pdf",
            )
        )
        return ParseResult(
            success=True,
            file_format=FileFormat.PDF,
            documents=(
                _shell_document(hints.doc_type, file_hash=file_hash, page_count=len(page_texts)),
            ),
            warnings=tuple(warnings),
            layout_signature=signature,
            page_texts=tuple(page_texts),
        )

    if scanned_pages:
        warnings.append(
            ParseWarning(
                code="NO_TEXT_LAYER",
                message=(
                    "One or more pages still have no readable text after extraction; review the "
                    "document and its source pages."
                ),
                locator="pdf",
            )
        )
    line_evidence_with_bbox: list[dict[str, FieldEvidence]] = []
    for evidence_by_field in line_evidence:
        page_number = next(
            (evidence.page for evidence in evidence_by_field.values() if evidence.page), 1
        )
        words = all_words.get(page_number, [])
        updated: dict[str, FieldEvidence] = {}
        for field, evidence in evidence_by_field.items():
            if evidence.bbox is not None:
                updated[field] = evidence
            else:
                updated[field] = evidence.model_copy(
                    update={"bbox": _find_word_bbox(words, evidence.snippet or "")}
                )
        line_evidence_with_bbox.append(updated)

    document, build_warnings = build_document(
        doc_type=hints.doc_type,
        header_raw=header_raw,
        line_raw=raw_line_rows,
        header_extras={},
        line_extras=({},) * len(raw_line_rows),
        header_alternatives=header_alternatives,
        locator="pdf",
        source=FieldSource.RULE,
        base_confidence=_BASE_CONFIDENCE,
        same_file_dates=(header_raw["doc_date"],) if header_raw.get("doc_date") else (),
        uom_aliases=hints.uom_aliases,
        charge_keywords=hints.charge_keywords,
    )
    document = _attach_evidence(document, header_evidence, line_evidence_with_bbox, ocr_pages)
    warnings.extend(build_warnings)
    if inferred_vendor_name and document.header.vendor_name is not None:
        inferred_vendor_name_field = document.header.vendor_name.model_copy(
            update={"confidence": Decimal("0.65")}
        )
        document = document.model_copy(
            update={
                "header": document.header.model_copy(
                    update={"vendor_name": inferred_vendor_name_field}
                ),
                "needs_verification": True,
                "reason_codes": tuple(
                    dict.fromkeys((*document.reason_codes, ReasonCode.LOW_CONFIDENCE_DATA))
                ),
            }
        )
        warnings.append(
            ParseWarning(
                code="SELLER_NAME_INFERRED_FROM_HEADER_BLOCK",
                message=(
                    "The seller name was read from the party heading "
                    "aligned with the seller GSTIN; "
                    "verify the identity before reconciliation."
                ),
                locator="pdf",
            )
        )
    if (
        inferred_po_reference
        and isinstance(document.header, InvoiceHeader)
        and (po_refs := document.header.po_refs) is not None
    ):
        invoice_header = document.header
        inferred_po_refs_field = po_refs.model_copy(update={"confidence": Decimal("0.60")})
        document = document.model_copy(
            update={
                "header": invoice_header.model_copy(update={"po_refs": inferred_po_refs_field}),
                "needs_verification": True,
                "reason_codes": tuple(
                    dict.fromkeys((*document.reason_codes, ReasonCode.LOW_CONFIDENCE_DATA))
                ),
            }
        )
        warnings.append(
            ParseWarning(
                code="PO_REFERENCE_INFERRED_FROM_DOCUMENT_TEXT",
                message=(
                    "A PO-looking reference was found in the invoice text without a formal PO "
                    "reference label; verify it before linking."
                ),
                locator="pdf",
            )
        )
    if amount_words_conflict:
        warnings.append(
            ParseWarning(
                code="AMOUNT_IN_WORDS_CONFLICT",
                message=(
                    "The payable amount in words does not agree with the extracted taxable value "
                    "and taxes; the parser left grand total unset."
                ),
                locator="pdf",
            )
        )
        document = document.model_copy(
            update={
                "needs_verification": True,
                "reason_codes": tuple(
                    dict.fromkeys((*document.reason_codes, ReasonCode.LOW_CONFIDENCE_DATA))
                ),
            }
        )
    if amount_words_uncrosschecked:
        warnings.append(
            ParseWarning(
                code="AMOUNT_IN_WORDS_UNCROSSCHECKED",
                message=(
                    "Grand total was extracted from the invoice amount-in-words field but could "
                    "not be cross-checked against parsed taxes."
                ),
                locator="pdf",
            )
        )
        document = document.model_copy(
            update={
                "needs_verification": True,
                "reason_codes": tuple(
                    dict.fromkeys((*document.reason_codes, ReasonCode.LOW_CONFIDENCE_DATA))
                ),
            }
        )
    if ocr_pages:
        warnings.append(
            ParseWarning(
                code="OCR_REVIEW_REQUIRED",
                message="OCR-extracted fields require human verification before reconciliation.",
                locator="pdf",
            )
        )
        document = document.model_copy(
            update={
                "needs_verification": True,
                "reason_codes": tuple(
                    dict.fromkeys((*document.reason_codes, ReasonCode.LOW_CONFIDENCE_DATA))
                ),
            }
        )
    if scanned_pages:
        document = document.model_copy(
            update={
                "needs_verification": True,
                "reason_codes": tuple(
                    dict.fromkeys((*document.reason_codes, ReasonCode.NO_TEXT_LAYER))
                ),
            }
        )
    return ParseResult(
        success=True,
        file_format=FileFormat.PDF,
        documents=(document,),
        warnings=tuple(warnings),
        layout_signature=signature,
        page_texts=tuple(page_texts),
    )


__all__ = ["parse_pdf"]
