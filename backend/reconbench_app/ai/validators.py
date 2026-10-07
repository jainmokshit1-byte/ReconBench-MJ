"""Grounding and safety validators applied to every parsed provider output.

A suggestion survives only if its quote occurs on the stated page of the text
that was actually sent, the value occurs inside that quote, nothing refers to a
redaction placeholder, and it does not conflict with another suggestion.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from reconbench_core.normalize.dates import parse_date
from reconbench_core.normalize.numbers import parse_decimal

from reconbench_app.ai.contracts import (
    DocTypeName,
    FieldKind,
    H1Output,
    H2Output,
    H3Output,
    H4Output,
    InvalidOutput,
    h1_field_kind,
    typed_value,
)
from reconbench_app.ai.prompts import H2PairText
from reconbench_app.ai.redaction import RedactedText, contains_placeholder

MULTI_VALUE_FIELDS = frozenset({"po_refs", "grn_refs"})
_WHITESPACE = re.compile(r"\s+")
_NUMBER_TOKEN = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?")
_DATE_TOKEN = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
    r"|\d{1,2}[- ][A-Za-z]{3,9}[- ,]+\d{2,4}"
)


def find_quote(haystack: str, quote: str) -> tuple[int, int] | None:
    """Locate ``quote`` exactly, or with any whitespace run matching any whitespace."""

    position = haystack.find(quote)
    if position >= 0:
        return position, position + len(quote)
    words = quote.split()
    if not words:
        return None
    match = re.search(r"\s+".join(re.escape(word) for word in words), haystack)
    return match.span() if match else None


def _normalized(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip().casefold()


def _numbers_in(quote: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER_TOKEN.findall(quote):
        try:
            found.add(parse_decimal(token))
        except ValueError:
            continue
    return found


def _dates_in(quote: str) -> set[date]:
    found: set[date] = set()
    for token in _DATE_TOKEN.findall(quote):
        candidate = re.sub(r"[ ,]+", "-", token.strip())
        variants = {candidate}
        slash = re.fullmatch(r"(\d{1,2})([./-])(\d{1,2})\2(\d{2,4})", candidate)
        if slash:
            # Ambiguous day/month order: either literal reading grounds the value.
            variants.add(
                f"{slash.group(3)}{slash.group(2)}{slash.group(1)}{slash.group(2)}{slash.group(4)}"
            )
        for variant in variants:
            try:
                found.add(parse_date(variant))
            except ValueError:
                continue
    return found


def value_in_quote(kind: FieldKind, value: str, typed: object, quote: str) -> bool:
    if kind in {"money", "number"}:
        return typed in _numbers_in(quote)
    if kind == "date":
        return typed in _dates_in(quote)
    return _normalized(value) in _normalized(quote)


@dataclass(frozen=True, slots=True)
class GroundedSuggestion:
    scope: Literal["header", "line"]
    line_no: int | None
    field: str
    kind: FieldKind
    value: str
    typed: str | Decimal | date
    page: int
    quote: str
    original_start: int
    original_end: int
    # Vision readings (h6_vision) carry a box instead of a text offset: ``bbox`` in
    # pixels of the working page image, ``box_2d`` on the 0–1000 page scale.
    bbox: tuple[int, int, int, int] | None = None
    box_2d: tuple[int, int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class H1Validation:
    accepted: tuple[GroundedSuggestion, ...]
    dropped: Mapping[str, int]


def validate_h1(
    output: H1Output, *, doc_type: DocTypeName, pages: Mapping[int, RedactedText]
) -> H1Validation:
    dropped: Counter[str] = Counter()
    candidates: list[GroundedSuggestion] = []
    for item in output.suggestions:
        kind = h1_field_kind(doc_type, item.scope, item.field)
        if kind is None:
            dropped["field_not_allowed"] += 1
            continue
        if (item.scope == "header") != (item.line_no is None):
            dropped["bad_line_no"] += 1
            continue
        if contains_placeholder(item.value) or contains_placeholder(item.quote):
            dropped["placeholder"] += 1
            continue
        try:
            typed = typed_value(kind, item.value)
        except ValueError:
            dropped["bad_value"] += 1
            continue
        page = pages.get(item.page)
        if page is None:
            dropped["bad_page"] += 1
            continue
        location = find_quote(page.text, item.quote)
        if location is None:
            dropped["quote_not_on_page"] += 1
            continue
        original = page.to_original(*location)
        if original is None:
            dropped["quote_in_redaction"] += 1
            continue
        if not value_in_quote(kind, item.value, typed, item.quote):
            dropped["value_not_in_quote"] += 1
            continue
        candidates.append(
            GroundedSuggestion(
                scope=item.scope,
                line_no=item.line_no,
                field=item.field,
                kind=kind,
                value=item.value,
                typed=typed,
                page=item.page,
                quote=item.quote,
                original_start=original[0],
                original_end=original[1],
            )
        )

    by_target: dict[tuple[str, int | None, str], list[GroundedSuggestion]] = {}
    for suggestion in candidates:
        key = (suggestion.scope, suggestion.line_no, suggestion.field)
        by_target.setdefault(key, []).append(suggestion)
    accepted: list[GroundedSuggestion] = []
    for (_scope, _line, field), group in by_target.items():
        distinct = {suggestion.typed for suggestion in group}
        if len(distinct) > 1 and field not in MULTI_VALUE_FIELDS:
            dropped["conflict"] += len(group)
            continue
        seen: set[object] = set()
        for suggestion in group:
            if suggestion.typed in seen:
                dropped["duplicate"] += 1
                continue
            seen.add(suggestion.typed)
            accepted.append(suggestion)
    if output.suggestions and not accepted:
        raise InvalidOutput("ungrounded", "no suggestion was grounded in the sent text")
    return H1Validation(tuple(accepted), MappingProxyType(dict(dropped)))


@dataclass(frozen=True, slots=True)
class GroundedOpinion:
    pair_id: str
    opinion: Literal["same", "different", "unsure"]
    reason: str


def validate_h2(
    output: H2Output, *, pairs: Mapping[str, H2PairText], excluded: frozenset[str] = frozenset()
) -> tuple[tuple[GroundedOpinion, ...], Mapping[str, int]]:
    dropped: Counter[str] = Counter()
    counts = Counter(item.pair_id for item in output.opinions)
    accepted: list[GroundedOpinion] = []
    for item in output.opinions:
        pair = pairs.get(item.pair_id)
        if pair is None or item.pair_id in excluded:
            dropped["pair_not_sent"] += 1
            continue
        if counts[item.pair_id] > 1:
            dropped["duplicate_pair"] += 1
            continue
        if any(
            contains_placeholder(text) for text in (item.reason, item.invoice_quote, item.po_quote)
        ):
            dropped["placeholder"] += 1
            continue
        if find_quote(pair.invoice_text, item.invoice_quote) is None or (
            find_quote(pair.po_text, item.po_quote) is None
        ):
            dropped["ungrounded"] += 1
            continue
        accepted.append(GroundedOpinion(item.pair_id, item.opinion, item.reason))
    if output.opinions and not accepted:
        raise InvalidOutput("ungrounded", "no opinion was grounded in the sent text")
    return tuple(accepted), MappingProxyType(dict(dropped))


@dataclass(frozen=True, slots=True)
class H3Decision:
    po_document_id: UUID | None
    reason: str


def validate_h3(
    output: H3Output, *, candidates: Mapping[str, UUID], invoice_text: str
) -> H3Decision:
    if contains_placeholder(output.reason):
        raise InvalidOutput("invalid_output", "reason refers to a redaction placeholder")
    if output.choice == "abstain":
        return H3Decision(None, output.reason)
    if output.choice not in candidates:
        raise InvalidOutput("invalid_output", "choice is not in the candidate pool")
    if (
        output.quote is None
        or contains_placeholder(output.quote)
        or find_quote(invoice_text, output.quote) is None
    ):
        raise InvalidOutput("ungrounded", "choice is not supported by an invoice quote")
    return H3Decision(candidates[output.choice], output.reason)


@dataclass(frozen=True, slots=True)
class H4Decision:
    opinion: Literal["same", "different"]
    reason: str


def validate_h4(
    output: H4Output, *, vendor_a_text: str, vendor_b_text: str, pan_conflict: bool
) -> H4Decision:
    if pan_conflict:
        raise InvalidOutput("invalid_output", "PAN-conflict pairs never take an AI opinion")
    if contains_placeholder(output.reason) or contains_placeholder(output.quote):
        raise InvalidOutput("invalid_output", "output refers to a redaction placeholder")
    if find_quote(vendor_a_text, output.quote) is None and (
        find_quote(vendor_b_text, output.quote) is None
    ):
        raise InvalidOutput("ungrounded", "quote does not occur in either vendor record")
    return H4Decision(output.opinion, output.reason)


__all__ = [
    "MULTI_VALUE_FIELDS",
    "GroundedOpinion",
    "GroundedSuggestion",
    "H1Validation",
    "H3Decision",
    "H4Decision",
    "find_quote",
    "validate_h1",
    "validate_h2",
    "validate_h3",
    "validate_h4",
    "value_in_quote",
]
