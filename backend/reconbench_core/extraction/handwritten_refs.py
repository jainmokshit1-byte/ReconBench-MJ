"""Typed handwritten references and logged misread normalisation (accuracy plan WI-Q2).

A handwritten or stamped PO or GRN number is an annotation, never a header field (MG-9).
This module turns one annotation into typed references (``po``, ``grn``, ``service_po``,
``other``) and repairs common misreads deterministically. Every rule that changed the
text is recorded with the text before and after (decision-log D88/D91/D92).

A normalised value must match its pattern and fit the document date: a PO (or service
PO) in the invoice's financial year or the one before, with a month no later than one
month after the invoice month; a GRN or an untyped short number within one month of the
invoice month. A reference that fails keeps its written text, ``normalised`` stays
``None`` and the matcher never uses it. A reference whose type is unclear is ``other``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

NORMALISATION_VERSION = "handwritten_refs.v1"

RefType = Literal["po", "grn", "service_po", "other"]
TypeSource = Literal["model", "marker", "pattern", "none"]

LOOKS_TO_TYPE: Mapping[str, RefType] = {
    "po_number": "po",
    "grn_number": "grn",
    "service_po_number": "service_po",
}
_SHORT = re.compile(r"(\d{2})-(\d{1,4})$")


@dataclass(frozen=True, slots=True)
class NormalisationStep:
    rule: str
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class TypedRef:
    """One reference read from one annotation."""

    type: RefType
    text: str
    """The reference as written (after whitespace cleanup only)."""
    normalised: str | None
    """Canonical form, or ``None`` when it fails the pattern or date checks."""
    short: str | None
    """The identifying ``MM-NNN`` part of ``normalised``."""
    type_source: TypeSource
    steps: tuple[NormalisationStep, ...] = ()
    page: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "normalised": self.normalised,
            "short": self.short,
            "type_source": self.type_source,
            "steps": [
                {"rule": step.rule, "before": step.before, "after": step.after}
                for step in self.steps
            ],
            "page": self.page,
            "version": NORMALISATION_VERSION,
        }


# --------------------------------------------------------------------------- normalising


def _valid_month(value: str) -> bool:
    return value.isdigit() and 1 <= int(value) <= 12


def _apply(text: str, rule: str, pattern: str, replace: Any, steps: list[NormalisationStep]) -> str:
    after = re.sub(pattern, replace, text)
    if after != text:
        steps.append(NormalisationStep(rule, text, after))
    return after


def _slash_as_one(match: re.Match[str]) -> str:
    # "26-27108-081": the slash after the financial year was read as "1".
    return f"{match.group(1)}/{match.group(2)}-" if _valid_month(match.group(2)) else match.group(0)


def _slash_one_month(match: re.Match[str]) -> str:
    # "26-27/108-081": a stray "1" after the slash.
    month = match.group(2)
    return f"{match.group(1)}/{month}-" if _valid_month(month) else match.group(0)


def normalise_text(text: str) -> tuple[str, tuple[NormalisationStep, ...]]:
    """The annotation text with common misreads repaired, and the steps taken."""

    steps: list[NormalisationStep] = []
    value = " ".join(text.split())
    value = _apply(value, "spaces_around_separators", r"\s*([-/])\s*", r"\1", steps)
    value = _apply(value, "fy_space", r"\b(\d{2}) (\d{2})(?=[/1]\d{2}-)", r"\1-\2", steps)
    # Repair the year/month separator first: the prefix rule looks for "YY-YY/MM-".
    value = _apply(value, "slash_read_as_1", r"(\d{2}-\d{2})1(\d{2})-", _slash_as_one, steps)
    value = _apply(
        value, "stray_1_after_slash", r"(\d{2}-\d{2})/1(\d{2})-", _slash_one_month, steps
    )
    value = _apply(value, "split_serial", r"(?<=\d{2}-\d{2})-(\d)(?!\d)", r"\1", steps)
    value = _apply(
        value,
        "po_prefix",
        r"(?<![A-Za-z0-9])(?:P[O0o]|0)\s*[/1]?\s*(?=\d{2}-\d{2}/\d{2}-)",
        "PO/",
        steps,
    )
    return value, tuple(steps)


# --------------------------------------------------------------------------- date checks


def _fy(on: date) -> tuple[int, int]:
    start = on.year if on.month >= 4 else on.year - 1
    return start % 100, (start + 1) % 100


def _fy_month_index(month: int) -> int:
    """April = 0 … March = 11."""

    return (month - 4) % 12


def _po_fits(fy_start: int, fy_end: int, month: int, on: date | None) -> bool:
    if (fy_start + 1) % 100 != fy_end or not 1 <= month <= 12:
        return False
    if on is None:
        return True
    invoice_start, _ = _fy(on)
    if fy_start == invoice_start:
        return _fy_month_index(month) <= _fy_month_index(on.month) + 1
    return fy_start == (invoice_start - 1) % 100


def _near_invoice_month(month: int, on: date | None) -> bool:
    if not 1 <= month <= 12:
        return False
    if on is None:
        return True
    distance = abs(month - on.month) % 12
    return min(distance, 12 - distance) <= 1


# --------------------------------------------------------------------------- extraction

_PO_LONG = re.compile(r"(?<![A-Za-z0-9])PO/(\d{2})-(\d{2})/(\d{2})-(\d{3})(?!\d)")
_SERVICE = re.compile(r"servicepur/(\d{2})-(\d{2})/(\d{2})-(\d{2,3})(?!\d)", re.IGNORECASE)
_GRN_LONG = re.compile(r"GRN/([A-Z]+)/(\d{2})-(\d{2})/(\d{2})-(\d{3})(?!\d)", re.IGNORECASE)
_BARE_LONG = re.compile(r"(?<![A-Za-z0-9/-])(\d{2})-(\d{2})/(\d{2})-(\d{3})(?!\d)")
_PARTIAL_LONG = re.compile(r"(?<![A-Za-z0-9/-])(\d{2})/(\d{2})-(\d{3})(?!\d)")
_SHORT_FORM = re.compile(r"(?<![A-Za-z0-9/-])(\d{2})-(\d{3})(?!\d)")


def _ref(
    ref_type: RefType,
    text: str,
    normalised: str | None,
    source: TypeSource,
    steps: tuple[NormalisationStep, ...],
    page: int | None,
) -> TypedRef:
    short_match = _SHORT.search(normalised) if normalised else None
    short = f"{short_match.group(1)}-{short_match.group(2)}" if short_match else None
    return TypedRef(ref_type, text, normalised, short, source, steps, page)


def refs_from_annotation(
    text: str,
    looks_like: str | None,
    *,
    doc_date: date | None,
    page: int | None = None,
) -> tuple[TypedRef, ...]:
    """Typed references in one annotation's text (``()`` when none is reference-like)."""

    cleaned, steps = normalise_text(text)
    model_type = LOOKS_TO_TYPE.get(looks_like or "")
    found: list[tuple[int, TypedRef]] = []
    taken: list[tuple[int, int]] = []

    def free(match: re.Match[str]) -> bool:
        return all(match.end() <= start or match.start() >= end for start, end in taken)

    for match in _SERVICE.finditer(cleaned):
        start, end, month = int(match.group(1)), int(match.group(2)), int(match.group(3))
        ok = _po_fits(start, end, month, doc_date)
        value = f"SERVICEPUR/{match.group(1)}-{match.group(2)}/{match.group(3)}-{match.group(4)}"
        found.append(
            (
                match.start(),
                _ref("service_po", match.group(0), value if ok else None, "marker", steps, page),
            )
        )
        taken.append(match.span())
    for match in _GRN_LONG.finditer(cleaned):
        if not free(match):
            continue
        month = int(match.group(4))
        ok = _near_invoice_month(month, doc_date)
        value = (
            f"GRN/{match.group(1).upper()}/{match.group(2)}-{match.group(3)}/"
            f"{match.group(4)}-{match.group(5)}"
        )
        found.append(
            (
                match.start(),
                _ref("grn", match.group(0), value if ok else None, "marker", steps, page),
            )
        )
        taken.append(match.span())
    for pattern, source in ((_PO_LONG, "marker"), (_BARE_LONG, "pattern")):
        for match in pattern.finditer(cleaned):
            if not free(match):
                continue
            start, end, month = int(match.group(1)), int(match.group(2)), int(match.group(3))
            ok = _po_fits(start, end, month, doc_date)
            value = f"PO/{match.group(1)}-{match.group(2)}/{match.group(3)}-{match.group(4)}"
            ref_type: RefType = "po"
            type_source: TypeSource = source  # type: ignore[assignment]
            if source == "pattern" and model_type in {"grn", "service_po"}:
                ref_type, type_source = "other", "none"  # the model said otherwise: unclear
            found.append(
                (
                    match.start(),
                    _ref(ref_type, match.group(0), value if ok else None, type_source, steps, page),
                )
            )
            taken.append(match.span())
    if doc_date is not None:
        _, invoice_end = _fy(doc_date)
        for match in _PARTIAL_LONG.finditer(cleaned):
            if not free(match) or int(match.group(1)) != invoice_end:
                continue
            start = (invoice_end - 1) % 100
            month = int(match.group(2))
            value = f"PO/{start:02d}-{match.group(1)}/{match.group(2)}-{match.group(3)}"
            completed = (
                *steps,
                NormalisationStep("fy_completed_from_invoice_date", match.group(0), value),
            )
            ok = _po_fits(start, invoice_end, month, doc_date)
            found.append(
                (
                    match.start(),
                    _ref("po", match.group(0), value if ok else None, "pattern", completed, page),
                )
            )
            taken.append(match.span())
    shorts = [match for match in _SHORT_FORM.finditer(cleaned) if free(match)]
    for match in shorts:
        month = int(match.group(1))
        ok = _near_invoice_month(month, doc_date)
        short_value = f"{match.group(1)}-{match.group(2)}" if ok else None
        # A short number is typed only when it is the annotation's only reference and the
        # model typed the annotation; otherwise its type is unclear (decision D88).
        if model_type is not None and not found and len(shorts) == 1:
            found.append(
                (match.start(), _ref(model_type, match.group(0), short_value, "model", steps, page))
            )
        else:
            found.append(
                (match.start(), _ref("other", match.group(0), short_value, "none", steps, page))
            )
    if model_type is not None and len(found) == 1 and found[0][1].type_source == "pattern":
        only = found[0][1]
        if only.type == model_type:
            found[0] = (
                found[0][0],
                _ref(model_type, only.text, only.normalised, "model", only.steps, page),
            )
    return tuple(ref for _, ref in sorted(found, key=lambda item: item[0]))


def typed_refs(
    annotations: Iterable[Mapping[str, Any]], *, doc_date: date | None
) -> tuple[TypedRef, ...]:
    """Typed references of every handwritten or stamped annotation of one document."""

    output: list[TypedRef] = []
    for note in annotations:
        if str(note.get("kind")) not in {"handwritten", "stamp"}:
            continue
        page = note.get("page")
        output.extend(
            refs_from_annotation(
                str(note.get("text") or ""),
                str(note.get("looks_like") or ""),
                doc_date=doc_date,
                page=page if isinstance(page, int) else None,
            )
        )
    return tuple(output)


def short_of(document_number: str | None) -> str | None:
    """The identifying ``MM-NNN`` part of a PO or GRN number (``None`` when it has none)."""

    match = _SHORT.search((document_number or "").strip())
    return f"{match.group(1)}-{match.group(2)}" if match else None


__all__ = [
    "LOOKS_TO_TYPE",
    "NORMALISATION_VERSION",
    "NormalisationStep",
    "RefType",
    "TypedRef",
    "normalise_text",
    "refs_from_annotation",
    "short_of",
    "typed_refs",
]
