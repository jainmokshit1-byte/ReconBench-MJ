"""Pure text, unit-of-measure, charge-line, and document-number normalizers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from reconbench_core.schema import LineType

_LEGAL_VENDOR_WORDS = {"pvt", "private", "ltd", "limited", "llp"}
_DEFAULT_UOM_ALIASES = {
    "NOS": "NOS",
    "PCS": "NOS",
    "EA": "NOS",
    "UNIT": "NOS",
    "KG": "KG",
    "KGS": "KG",
    "MTR": "MTR",
    "M": "MTR",
}
DEFAULT_CHARGE_KEYWORDS = (
    "freight",
    "packing",
    "insurance",
    "handling",
    "round off",
    "round-off",
    "discount",
)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_SPEC_PATTERNS = (
    re.compile(r"(?<![a-z0-9])[a-z]{1,6}\s*-?\s*\d{2,}[a-z0-9]*(?:[-/][a-z0-9]+)*", re.I),
    re.compile(r"(?<!\w)\d+(?:\.\d+)?\s*(?:gb|tb)\b", re.I),
    re.compile(r"(?<!\w)\d+(?:\.\d+)?\s*(?:inches|inch|in)\b", re.I),
    re.compile(r'(?<!\w)\d+(?:\.\d+)?\s*"', re.I),
    re.compile(r"(?<!\w)\d+(?:\.\d+)?\s*kw\b", re.I),
    re.compile(
        r"(?<!\w)\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?"
        r"(?:\s*[x×]\s*\d+(?:\.\d+)?)?\s*(?:mm|cm|m)?\b",
        re.I,
    ),
)
_COLORS = {
    "black",
    "blue",
    "brown",
    "gold",
    "green",
    "grey",
    "gray",
    "orange",
    "pink",
    "purple",
    "red",
    "silver",
    "white",
    "yellow",
}


@dataclass(frozen=True)
class UOMResult:
    canonical: str | None
    is_known: bool


@dataclass(frozen=True)
class NormalizedItem:
    description: str
    tokens: tuple[str, ...]
    spec_tokens: tuple[str, ...]


def normalize_vendor_name(value: str) -> str:
    """Case-fold a vendor name and remove common Indian legal suffixes/prefixes."""

    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"\bm\s*/\s*s\b", " ", text)
    tokens = _WORD.findall(text)
    return " ".join(token for token in tokens if token not in _LEGAL_VENDOR_WORDS)


def normalize_item_text(value: str) -> NormalizedItem:
    """Tokenize item text and separately retain product identity/specification tokens."""

    text = unicodedata.normalize("NFKC", value).casefold()
    spec_tokens: set[str] = set()
    for pattern in _SPEC_PATTERNS:
        for match in pattern.finditer(text):
            token = match.group(0).casefold().replace("×", "x")
            token = re.sub(r"\s*([x-])\s*", r"\1", token)
            token = re.sub(r"\s+", "", token)
            spec_tokens.add(token)

    words = _WORD.findall(text)
    spec_tokens.update(word for word in words if word in _COLORS)
    return NormalizedItem(
        description=" ".join(words),
        tokens=tuple(words),
        spec_tokens=tuple(sorted(spec_tokens)),
    )


def _normalize_unit_label(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def normalize_uom(value: str, aliases: Mapping[str, str] | None = None) -> UOMResult:
    """Map a known UOM alias or explicitly report an unknown unit for review."""

    normalized_aliases = {
        _normalize_unit_label(key): _normalize_unit_label(target)
        for key, target in _DEFAULT_UOM_ALIASES.items()
    }
    if aliases is not None:
        normalized_aliases.update(
            {
                _normalize_unit_label(key): _normalize_unit_label(target)
                for key, target in aliases.items()
            }
        )
    raw = _normalize_unit_label(value)
    canonical = normalized_aliases.get(raw)
    return UOMResult(canonical=canonical, is_known=canonical is not None)


def classify_line_type(
    description: str, charge_keywords: tuple[str, ...] = DEFAULT_CHARGE_KEYWORDS
) -> LineType:
    """Classify known charge descriptions; unmatched lines remain goods."""

    normalized = " ".join(_WORD.findall(unicodedata.normalize("NFKC", description).casefold()))
    padded = f" {normalized} "
    for keyword in charge_keywords:
        phrase = " ".join(_WORD.findall(unicodedata.normalize("NFKC", keyword).casefold()))
        if phrase and f" {phrase} " in padded:
            return LineType.CHARGE
    return LineType.GOODS


def canonicalize_po_number(value: str) -> str:
    """Remove separators and leading zeroes in numeric chunks of a PO reference."""

    segments = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKC", value))
    canonical: list[str] = []
    for segment in segments:
        if segment.isascii() and segment.isdigit():
            canonical.append(segment.lstrip("0") or "0")
        else:
            canonical.append(segment.upper())
    return "".join(canonical)


def canonicalize_document_number(value: str) -> str:
    """Canonicalize invoice, GRN, challan, and PO references by the same rule."""

    return canonicalize_po_number(value)
