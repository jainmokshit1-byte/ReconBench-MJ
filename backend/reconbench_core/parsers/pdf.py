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
from reconbench_core.parsers.types import PageText, ParseResult, ParserHints, ParseWarning
from reconbench_core.schema import (
    DocType,
    Document,
    FieldEvidence,
    FieldSource,
    FieldValue,
    FileFormat,
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
    synonyms: Mapping[str, Sequence[str]], allowed: set[str]
) -> list[tuple[tuple[str, ...], str]]:
    aliases: list[tuple[tuple[str, ...], str]] = []
    for field, labels in synonyms.items():
        if field not in allowed:
            continue
        for label in (field, *labels):
            tokens = tuple(normalize_label(label).split())
            if tokens:
                aliases.append((tokens, field))
    return sorted(set(aliases), key=lambda item: (-len(item[0]), item[0], item[1]))


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


def _line_value(
    groups: Sequence[Sequence[Mapping[str, Any]]],
    group_index: int,
    end_word: int,
    aliases: Sequence[tuple[tuple[str, ...], str]],
) -> tuple[str, list[Mapping[str, Any]]] | None:
    same_line = list(groups[group_index][end_word + 1 :])
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
    vertical_gap = float(next_line[0]["top"]) - float(groups[group_index][-1]["bottom"])
    if vertical_gap > 36 or _match_label(next_line, aliases) is not None:
        return None
    value = " ".join(str(word.get("text", "")) for word in next_line).strip(" :-–—#")
    if not value:
        return None
    return value, next_line


def _extract_header_page(
    groups: Sequence[Sequence[Mapping[str, Any]]],
    aliases: Sequence[tuple[tuple[str, ...], str]],
) -> list[tuple[str, str, list[Mapping[str, Any]], str]]:
    found: list[tuple[str, str, list[Mapping[str, Any]], str]] = []
    for group_index, group in enumerate(groups):
        match = _match_label(group, aliases)
        if match is None:
            continue
        field, start_word, end_word = match
        reading = _line_value(groups, group_index, end_word, aliases)
        if reading is not None:
            value, value_words = reading
            label = normalize_label(
                " ".join(str(word.get("text", "")) for word in group[start_word : end_word + 1])
            )
            found.append((field, value, value_words, label))
    return found


def _extract_header_text_lines(
    text: str,
    aliases: Sequence[tuple[tuple[str, ...], str]],
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
            label = " ".join(alias)
            matches.append((start, end, field, label))
            index += len(alias)

        for match_index, (_start, end, field, label) in enumerate(matches):
            next_start = (
                matches[match_index + 1][0]
                if match_index + 1 < len(matches)
                else len(line)
            )
            value = line[end:next_start].strip(" \t.:#–—-")
            if value:
                found.append((field, value, label))
    return found


def _header_reading_priority(doc_type: DocType, field: str, label: str) -> int:
    """Prefer labels for the selected document when PDFs bundle other records."""

    normalized = normalize_label(label)
    if field == "doc_date":
        if doc_type is DocType.INVOICE:
            if "INVOICE" in normalized and "DATE" in normalized:
                return 50
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
    return bool(value.strip())


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
    header_aliases = _field_aliases(synonyms, _HEADER_FIELDS_BY_TYPE[hints.doc_type])
    line_alias_map = alias_to_field(synonyms, _LINE_FIELDS)
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
                    groups, header_aliases
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
                    for field, value, label in _extract_header_text_lines(text, header_aliases):
                        if not _valid_text_header_reading(field, value):
                            continue
                        header_names.add(field)
                        snippet = value.strip()
                        priority = (
                            _header_reading_priority(hints.doc_type, field, label) + 100
                        )
                        if field not in header_raw or priority > header_priorities[field]:
                            if field in header_raw and header_raw[field] != snippet:
                                header_alternatives.setdefault(field, []).append(header_raw[field])
                            header_raw[field] = snippet
                            header_evidence[field] = _evidence(page_number, snippet, [])
                            header_priorities[field] = priority
                        elif snippet != header_raw[field]:
                            alternatives = header_alternatives.setdefault(field, [])
                            if snippet not in alternatives:
                                alternatives.append(snippet)
                table_rows, found_headers = _extract_tables(
                    page, page_number, line_alias_map, line_evidence
                )
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
