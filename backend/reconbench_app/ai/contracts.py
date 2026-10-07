"""Strict provider output contracts for each AI hook (Task 4.17).

Every model forbids extra keys, so status, verdict, approval or match fields in a
provider response reject the whole output. Money and quantity suggestions are
decimal strings that must parse exactly; nothing here can express a verdict.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from reconbench_core.extraction.vision import (
    ANNOTATION_KINDS,
    ANNOTATION_LOOKS,
    DOC_KINDS,
    FIELD_KINDS,
    LINE_CELLS,
    PAGE_KINDS,
)

HookName = Literal["h1_extract", "h2_item", "h3_rerank", "h4_vendor", "h6_vision"]
DocTypeName = Literal["invoice", "po", "grn"]
FieldKind = Literal["text", "ref", "date", "money", "number", "gstin", "currency"]

SCHEMA_VERSIONS: Mapping[str, str] = MappingProxyType(
    {
        "h1_extract": "h1.schema.v2",
        "h2_item": "h2.schema.v2",
        "h3_rerank": "h3.schema.v1",
        "h4_vendor": "h4.schema.v1",
        "h6_vision": "vision.schema.v3",
        "provider_test": "provider_test.schema.v1",
    }
)
MAX_OUTPUT_TEXT_BYTES = 65_536
# A page-image reading of a long invoice is larger than any text hook answer.
OUTPUT_BYTE_LIMITS: Mapping[str, int] = MappingProxyType({"h6_vision": 196_608})

# Never accepted as suggestion targets in any hook (decision-making fields).
FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "approval",
        "approved",
        "case_status",
        "match",
        "matched",
        "po_status",
        "revision",
        "status",
        "verdict",
    }
)

_COMMON_HEADER: dict[str, FieldKind] = {
    "doc_no": "ref",
    "doc_date": "date",
    "vendor_name": "text",
    "vendor_gstin": "gstin",
}
_TOTALS: dict[str, FieldKind] = {
    "buyer_gstin": "gstin",
    "currency": "currency",
    "taxable_total": "money",
    "cgst": "money",
    "sgst": "money",
    "igst": "money",
    "round_off": "money",
    "grand_total": "money",
}
_PRICED_LINE: dict[str, FieldKind] = {
    "description": "text",
    "item_code": "ref",
    "hsn_sac": "ref",
    "uom": "ref",
    "quantity": "number",
    "rate": "number",
    "amount": "money",
}

H1_HEADER_FIELDS: Mapping[str, Mapping[str, FieldKind]] = MappingProxyType(
    {
        "invoice": MappingProxyType(
            {
                **_COMMON_HEADER,
                **_TOTALS,
                "po_refs": "ref",
                "grn_refs": "ref",
                "challan_no": "ref",
                "e_way_bill_no": "ref",
                "irn": "ref",
                "place_of_supply": "text",
            }
        ),
        # Proposed PO/GRN allowlists (decision D13), awaiting Product approval.
        "po": MappingProxyType(
            {
                **_COMMON_HEADER,
                **_TOTALS,
                "ship_to": "text",
                "payment_terms": "text",
                "delivery_date": "date",
            }
        ),
        "grn": MappingProxyType(
            {
                **_COMMON_HEADER,
                "po_refs": "ref",
                "challan_no": "ref",
                "supplier_invoice_no": "ref",
            }
        ),
    }
)
H1_LINE_FIELDS: Mapping[str, Mapping[str, FieldKind]] = MappingProxyType(
    {
        "invoice": MappingProxyType(dict(_PRICED_LINE)),
        "po": MappingProxyType(dict(_PRICED_LINE)),
        "grn": MappingProxyType(
            {
                "description": "text",
                "item_code": "ref",
                "hsn_sac": "ref",
                "uom": "ref",
                "received_qty": "number",
                "rejected_qty": "number",
                "accepted_qty": "number",
                "po_ref": "ref",
                "po_line_ref": "ref",
            }
        ),
    }
)
MAX_H1_LINE_NO = 500

_MONEY = re.compile(r"-?\d{1,16}(?:\.\d{1,2})?\Z")
_NUMBER = re.compile(r"-?\d{1,14}(?:\.\d{1,4})?\Z")
_GSTIN = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class InvalidOutput(ValueError):
    """The provider output cannot be used; ``code`` is a sanitized outcome."""

    def __init__(self, code: Literal["invalid_output", "ungrounded"], reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class H1Suggestion(_Strict):
    scope: Literal["header", "line"]
    line_no: int | None = Field(default=None, ge=1, le=MAX_H1_LINE_NO)
    field: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=1_000)
    page: int = Field(ge=1, le=10_000)
    quote: str = Field(min_length=1, max_length=500)


class H1Output(_Strict):
    suggestions: list[H1Suggestion] = Field(max_length=200)


class H2Opinion(_Strict):
    pair_id: str = Field(pattern=r"^p[0-9]{1,2}$")
    opinion: Literal["same", "different", "unsure"]
    reason: str = Field(min_length=1, max_length=300)
    invoice_quote: str = Field(min_length=1, max_length=200)
    po_quote: str = Field(min_length=1, max_length=200)


class H2Output(_Strict):
    opinions: list[H2Opinion] = Field(max_length=20)


class H3Output(_Strict):
    choice: str = Field(pattern=r"^(c[1-5]|abstain)$")
    reason: str = Field(min_length=1, max_length=300)
    quote: str | None = Field(default=None, min_length=1, max_length=200)


class H4Output(_Strict):
    opinion: Literal["same", "different"]
    reason: str = Field(min_length=1, max_length=300)
    quote: str = Field(min_length=1, max_length=200)


class ProviderTestOutput(_Strict):
    status: Literal["ok"]


# A box that is not exactly four integers drops only its own item in the core check
# (``bad_box``); rejecting the whole answer lost 3 of 39 documents (D41).
_BOX = Field(min_length=1, max_length=16)
_TEXT = Field(min_length=1, max_length=500)


class VisionPageInfo(_Strict):
    page: int = Field(ge=1, le=10_000)
    kind: str = Field(min_length=1, max_length=40)
    title: str | None = Field(default=None, max_length=200)


class VisionFieldItem(_Strict):
    name: str = Field(min_length=1, max_length=40)
    text: str = _TEXT
    label: str | None = Field(default=None, max_length=200)
    page: int = Field(ge=1, le=10_000)
    box_2d: list[int] = _BOX


class VisionLineItem(_Strict):
    page: int = Field(ge=1, le=10_000)
    box_2d: list[int] = _BOX
    # Required keys since vision.schema.v2/v3 (AC-5b, D40): optional cells were left
    # out (amount in 10 of 109 rows) and other columns were pushed into ``uom``. An
    # empty string means the cell is not printed in that row.
    description: str = Field(max_length=500)
    quantity: str = Field(max_length=60)
    amount: str = Field(max_length=60)
    line_no: str | None = Field(default=None, max_length=20)
    item_code: str | None = Field(default=None, max_length=200)
    hsn_sac: str | None = Field(default=None, max_length=40)
    # A long unit no longer rejects the whole answer; the core check drops the cell.
    uom: str | None = Field(default=None, max_length=500)
    rate: str | None = Field(default=None, max_length=60)
    discount: str | None = Field(default=None, max_length=60)
    tax_rate: str | None = Field(default=None, max_length=40)
    cgst: str | None = Field(default=None, max_length=60)
    sgst: str | None = Field(default=None, max_length=60)
    igst: str | None = Field(default=None, max_length=60)
    received_qty: str | None = Field(default=None, max_length=60)
    accepted_qty: str | None = Field(default=None, max_length=60)
    rejected_qty: str | None = Field(default=None, max_length=60)
    serials: str | None = Field(default=None, max_length=500)
    po_ref: str | None = Field(default=None, max_length=100)


class VisionAnnotationItem(_Strict):
    kind: str = Field(min_length=1, max_length=20)
    text: str = _TEXT
    looks_like: str = Field(min_length=1, max_length=40)
    page: int = Field(ge=1, le=10_000)
    box_2d: list[int] = _BOX


class VisionOutput(_Strict):
    """A page-image reading (``vision.schema.v3``): every value as printed, with its box."""

    doc_kind: str = Field(min_length=1, max_length=40)
    pages: list[VisionPageInfo] = Field(max_length=50)
    fields: list[VisionFieldItem] = Field(max_length=400)
    lines: list[VisionLineItem] = Field(max_length=500)
    annotations: list[VisionAnnotationItem] = Field(max_length=60)


OUTPUT_MODELS: Mapping[str, type[_Strict]] = MappingProxyType(
    {
        "h1_extract": H1Output,
        "h2_item": H2Output,
        "h3_rerank": H3Output,
        "h4_vendor": H4Output,
        "h6_vision": VisionOutput,
        "provider_test": ProviderTestOutput,
    }
)


def _string(enum: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if enum is not None:
        schema["enum"] = enum
    return schema


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def h1_response_schema(doc_type: DocTypeName) -> dict[str, Any]:
    fields = sorted({*H1_HEADER_FIELDS[doc_type], *H1_LINE_FIELDS[doc_type]})
    suggestion = _object(
        {
            "scope": _string(enum=["header", "line"]),
            "line_no": {"type": "integer"},
            "field": _string(enum=fields),
            "value": _string(),
            "page": {"type": "integer"},
            "quote": _string(),
        },
        ["scope", "field", "value", "page", "quote"],
    )
    # Gemini rejects array ``maxItems`` with 400 INVALID_REQUEST (owner probe,
    # 6 Oct 2026); the list caps are enforced by ``H1Output``/``H2Output`` instead.
    return _object({"suggestions": {"type": "array", "items": suggestion}}, ["suggestions"])


H2_RESPONSE_SCHEMA: Mapping[str, Any] = MappingProxyType(
    _object(
        {
            "opinions": {
                "type": "array",
                "items": _object(
                    {
                        "pair_id": _string(),
                        "opinion": _string(enum=["same", "different", "unsure"]),
                        "reason": _string(),
                        "invoice_quote": _string(),
                        "po_quote": _string(),
                    },
                    ["pair_id", "opinion", "reason", "invoice_quote", "po_quote"],
                ),
            }
        },
        ["opinions"],
    )
)
H3_RESPONSE_SCHEMA: Mapping[str, Any] = MappingProxyType(
    _object(
        {
            "choice": _string(enum=["c1", "c2", "c3", "c4", "c5", "abstain"]),
            "reason": _string(),
            "quote": _string(),
        },
        ["choice", "reason"],
    )
)
H4_RESPONSE_SCHEMA: Mapping[str, Any] = MappingProxyType(
    _object(
        {
            "opinion": _string(enum=["same", "different"]),
            "reason": _string(),
            "quote": _string(),
        },
        ["opinion", "reason", "quote"],
    )
)


def vision_response_schema() -> dict[str, Any]:
    """Provider schema for ``h6_vision``: flat lists, no ``maxItems``/``$ref``/``anyOf`` (D31)."""

    box = {"type": "array", "items": {"type": "integer"}}
    # The model writes keys in schema order: the scored cells come first (D40).
    first = ("description", "quantity", "rate", "amount", "uom")
    line_cells = {name: _string() for name in (*first, *(n for n in LINE_CELLS if n not in first))}
    return _object(
        {
            "doc_kind": _string(enum=list(DOC_KINDS)),
            "pages": {
                "type": "array",
                "items": _object(
                    {
                        "page": {"type": "integer"},
                        "kind": _string(enum=list(PAGE_KINDS)),
                        "title": _string(),
                    },
                    ["page", "kind"],
                ),
            },
            "fields": {
                "type": "array",
                "items": _object(
                    {
                        "name": _string(enum=sorted(FIELD_KINDS)),
                        "text": _string(),
                        "label": _string(),
                        "page": {"type": "integer"},
                        "box_2d": box,
                    },
                    ["name", "text", "page", "box_2d"],
                ),
            },
            "lines": {
                "type": "array",
                "items": _object(
                    {"page": {"type": "integer"}, "box_2d": box, **line_cells},
                    ["page", "box_2d", "description", "quantity", "amount"],
                ),
            },
            "annotations": {
                "type": "array",
                "items": _object(
                    {
                        "kind": _string(enum=list(ANNOTATION_KINDS)),
                        "text": _string(),
                        "looks_like": _string(enum=list(ANNOTATION_LOOKS)),
                        "page": {"type": "integer"},
                        "box_2d": box,
                    },
                    ["kind", "text", "looks_like", "page", "box_2d"],
                ),
            },
        },
        ["doc_kind", "pages", "fields", "lines", "annotations"],
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def parse_output(kind: str, output_text: str) -> _Strict:
    """Parse provider JSON strictly; any deviation is ``invalid_output``."""

    model = OUTPUT_MODELS.get(kind)
    if model is None:
        raise InvalidOutput("invalid_output", "unknown output kind")
    if len(output_text.encode("utf-8")) > OUTPUT_BYTE_LIMITS.get(kind, MAX_OUTPUT_TEXT_BYTES):
        raise InvalidOutput("invalid_output", "output too large")
    try:
        raw = json.loads(output_text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise InvalidOutput("invalid_output", "output is not a single JSON object") from exc
    if not isinstance(raw, dict):
        raise InvalidOutput("invalid_output", "output is not a JSON object")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise InvalidOutput("invalid_output", "output violates the schema") from exc


def h1_field_kind(doc_type: DocTypeName, scope: str, field: str) -> FieldKind | None:
    if field in FORBIDDEN_FIELD_NAMES:
        return None
    table = H1_HEADER_FIELDS if scope == "header" else H1_LINE_FIELDS
    return table[doc_type].get(field)


def typed_value(kind: FieldKind, value: str) -> str | Decimal | date:
    """Convert a suggestion value to its domain type, or raise ``ValueError``."""

    text = value.strip()
    if not text or text != value:
        raise ValueError("value has surrounding whitespace or is blank")
    if kind in {"money", "number"}:
        pattern = _MONEY if kind == "money" else _NUMBER
        if not pattern.fullmatch(text):
            raise ValueError("value is not a plain decimal string")
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("value is not a decimal") from exc
    if kind == "date":
        if not _ISO_DATE.fullmatch(text):
            raise ValueError("dates must be YYYY-MM-DD")
        return date.fromisoformat(text)
    if kind == "gstin":
        if not _GSTIN.fullmatch(text):
            raise ValueError("value is not a GSTIN")
        return text
    if kind == "currency":
        if not _CURRENCY.fullmatch(text):
            raise ValueError("value is not a currency code")
        return text
    if kind == "ref" and len(text) > 256:
        raise ValueError("reference is too long")
    return text


__all__ = [
    "FORBIDDEN_FIELD_NAMES",
    "H1_HEADER_FIELDS",
    "H1_LINE_FIELDS",
    "H2_RESPONSE_SCHEMA",
    "H3_RESPONSE_SCHEMA",
    "H4_RESPONSE_SCHEMA",
    "MAX_H1_LINE_NO",
    "MAX_OUTPUT_TEXT_BYTES",
    "OUTPUT_BYTE_LIMITS",
    "OUTPUT_MODELS",
    "SCHEMA_VERSIONS",
    "DocTypeName",
    "FieldKind",
    "H1Output",
    "H1Suggestion",
    "H2Opinion",
    "H2Output",
    "H3Output",
    "H4Output",
    "HookName",
    "InvalidOutput",
    "ProviderTestOutput",
    "VisionAnnotationItem",
    "VisionFieldItem",
    "VisionLineItem",
    "VisionOutput",
    "VisionPageInfo",
    "h1_field_kind",
    "h1_response_schema",
    "parse_output",
    "typed_value",
    "vision_response_schema",
]
