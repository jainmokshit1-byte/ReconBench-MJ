"""Party roles and document-level GST checks shared by every parser.

Each parser's output passes through :func:`guard_result` before it leaves the core:

* the buyer's own GSTIN (``ParserHints.buyer_entities``) is never the supplier; it
  moves to ``buyer_gstin`` when that is empty;
* printed labels ("(Bill from)", "Supplier", "M/s"), text holding a GSTIN, and the
  buyer's own name are never the supplier name;
* the GST structure must fit the parties' states: an intra-state document charges
  CGST = SGST and no IGST, an inter-state document IGST only, and the total tax rate
  must lie within the configured GST slabs.

A failed check never invents a value. The field is dropped (parties) or kept (GST),
and the document goes to review with a warning that says why.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from reconbench_core.extraction.values import find_dates, gstins_in
from reconbench_core.normalize.gstin import validate_gstin
from reconbench_core.normalize.text import normalize_vendor_name
from reconbench_core.parsers.types import BuyerEntityHint, ParseResult, ParserHints, ParseWarning
from reconbench_core.schema import Document, DocumentHeader, FieldValue, ReasonCode

# Words that make up party labels, never a party's name on their own.
_LABEL_WORDS = frozenset(
    {
        "ADDRESS",
        "AND",
        "BILL",
        "BILLED",
        "BUYER",
        "BY",
        "CODE",
        "CONSIGNEE",
        "CONSIGNOR",
        "CUSTOMER",
        "DETAILS",
        "FOR",
        "FROM",
        "GSTIN",
        "INVOICE",
        "M",
        "MS",
        "NAME",
        "OF",
        "PARTY",
        "RECEIVER",
        "RECIPIENT",
        "S",
        "SELLER",
        "SHIP",
        "SHIPPED",
        "SOLD",
        "STATE",
        "SUPPLIER",
        "THE",
        "TO",
        "UIN",
        "VENDOR",
    }
)
# Tax and quantity words belong to item or summary rows, never to a party name.
_LINE_WORDS = re.compile(r"\b(CGST|SGST|IGST|UTGST|HSN|SAC|QTY)\b")
_NAME_MATCH = 0.88
_SPLIT_TOLERANCE = Decimal("0.02")
_RATE_TOLERANCE = Decimal("0.1")


def _gstin(value: str | None) -> str | None:
    if not value:
        return None
    facts = validate_gstin(value)
    return facts.normalized if facts.is_valid else None


def buyer_gstins(entities: Iterable[BuyerEntityHint]) -> frozenset[str]:
    return frozenset(gstin for item in entities if (gstin := _gstin(item.gstin)) is not None)


def is_buyer_gstin(value: str | None, entities: Sequence[BuyerEntityHint]) -> bool:
    gstin = _gstin(value)
    return gstin is not None and gstin in buyer_gstins(entities)


def _compact_name(value: str) -> str:
    return normalize_vendor_name(value).replace(" ", "")


def is_buyer_name(value: str | None, entities: Sequence[BuyerEntityHint]) -> bool:
    """The printed name is one of the buyer's own entity names (spacing and suffixes aside)."""

    if not value:
        return False
    name = _compact_name(value)
    if len(name) < 4:
        return False
    tokens = set(normalize_vendor_name(value).split())
    for entity in entities:
        own = _compact_name(entity.name)
        if len(own) < 4:
            continue
        if name == own or SequenceMatcher(None, name, own).ratio() >= _NAME_MATCH:
            return True
        # OCR dropped a word: "Benchmark Solutions" for "Benchmark Computer Solutions".
        if len(tokens) >= 2 and tokens <= set(normalize_vendor_name(entity.name).split()):
            return True
    return False


def name_problem(value: str | None) -> str | None:
    """Why a printed text cannot be a party name: a label, a GSTIN or a number."""

    if value is None:
        return None
    text = value.strip()
    tokens = re.findall(r"[A-Za-z0-9]+", text.upper())
    if not tokens:
        return "empty"
    if gstins_in(text):
        return "gstin"
    if all(token in _LABEL_WORDS for token in tokens):
        return "label"
    if "%" in text or _LINE_WORDS.search(text.upper()) or find_dates(text):
        return "line_text"
    words = [word for word in text.split() if word[:1].isalpha()]
    if words and sum(word[0].islower() for word in words) * 2 > len(words):
        # Running text ("before the said effective date"), not a business name.
        return "line_text"
    letters = sum(char.isalpha() for char in text)
    digits = sum(char.isdigit() for char in text)
    if letters < 3 or digits > letters:
        return "not_a_name"
    return None


def buyer_state(header: DocumentHeader, entities: Sequence[BuyerEntityHint]) -> str | None:
    """The buyer's GST state: the printed buyer GSTIN, else the one state all entities share."""

    printed = _gstin(header.buyer_gstin.value if header.buyer_gstin else None)
    if printed is not None:
        return printed[:2]
    states = {gstin[:2] for gstin in buyer_gstins(entities)}
    return next(iter(states)) if len(states) == 1 else None


def _amount(field: FieldValue[Any] | None) -> Decimal:
    if field is None or field.value is None:
        return Decimal("0")
    return abs(Decimal(field.value))


def gst_problems(
    document: Document, *, state: str | None, rates: Sequence[Decimal]
) -> list[tuple[str, str, str]]:
    """``(review field, warning code, message)`` for each GST structure rule that fails."""

    header = document.header
    cgst, sgst, igst = _amount(header.cgst), _amount(header.sgst), _amount(header.igst)
    problems: list[tuple[str, str, str]] = []
    if cgst and sgst and abs(cgst - sgst) > _SPLIT_TOLERANCE:
        problems.append(
            ("sgst", "GST_SPLIT_UNEQUAL", "CGST and SGST differ; intra-state tax is split equally.")
        )
    vendor = _gstin(header.vendor_gstin.value if header.vendor_gstin else None)
    if vendor is not None and state is not None and (cgst or sgst or igst):
        if vendor[:2] == state and igst:
            problems.append(
                (
                    "igst",
                    "GST_TYPE_MISMATCH",
                    "Supplier and buyer are in the same state, so IGST is not expected.",
                )
            )
        elif vendor[:2] != state and (cgst or sgst):
            problems.append(
                (
                    "cgst",
                    "GST_TYPE_MISMATCH",
                    "Supplier and buyer are in different states, so only IGST is expected.",
                )
            )
    taxable = _amount(header.taxable_total)
    tax = cgst + sgst + igst
    if taxable > 0 and tax > 0 and rates:
        percent = tax / taxable * 100
        if percent > max(rates) + _RATE_TOLERANCE or percent < min(rates) - _RATE_TOLERANCE:
            problems.append(
                (
                    "taxable_total",
                    "GST_RATE_OUT_OF_RANGE",
                    "Total tax divided by the taxable value is outside the configured GST slabs.",
                )
            )
        else:
            line_rates = {
                Decimal(line.tax_rate.value)
                for line in document.lines
                if line.tax_rate is not None and line.tax_rate.value is not None
            }
            if len(line_rates) == 1 and len(line_rates) == len(
                [line for line in document.lines if line.tax_rate is not None]
            ):
                (line_rate,) = line_rates
                if abs(percent - line_rate) > _RATE_TOLERANCE:
                    problems.append(
                        (
                            "taxable_total",
                            "GST_RATE_MISMATCH",
                            "The total tax does not equal the taxable value at the lines' rate.",
                        )
                    )
    return problems


def guard_document(document: Document, hints: ParserHints) -> tuple[Document, list[ParseWarning]]:
    entities = hints.buyer_entities
    header = document.header
    updates: dict[str, Any] = {}
    review: list[str] = []
    warnings: list[ParseWarning] = []
    vendor_gstin = header.vendor_gstin
    if vendor_gstin is not None and is_buyer_gstin(vendor_gstin.value, entities):
        updates["vendor_gstin"] = None
        if header.buyer_gstin is None:
            updates["buyer_gstin"] = vendor_gstin
        review.append("vendor_gstin")
        warnings.append(
            ParseWarning(
                code="VENDOR_GSTIN_IS_BUYER_ENTITY",
                message="The GSTIN read as the supplier's is the buyer's own; it was not used.",
            )
        )
    vendor_name = header.vendor_name
    if vendor_name is not None:
        problem = name_problem(vendor_name.value)
        if problem is None and is_buyer_name(vendor_name.value, entities):
            problem = "buyer"
        if problem is not None:
            updates["vendor_name"] = None
            review.append("vendor_name")
            warnings.append(
                ParseWarning(
                    code="VENDOR_NAME_REJECTED",
                    message=(
                        "The text read as the supplier name is the buyer's own name."
                        if problem == "buyer"
                        else "The text read as the supplier name is a label or a number."
                    ),
                )
            )
    if updates:
        header = header.model_copy(update=updates)
    buyer = _gstin(header.buyer_gstin.value if header.buyer_gstin else None)
    if buyer is not None and entities and buyer not in buyer_gstins(entities):
        warnings.append(
            ParseWarning(
                code="BUYER_GSTIN_NOT_CONFIGURED",
                message="The buyer GSTIN printed on the document is not a configured buyer entity.",
            )
        )
    document = document.model_copy(update={"header": header})
    for field, code, message in gst_problems(
        document, state=buyer_state(header, entities), rates=hints.gst_rates
    ):
        review.append(field)
        warnings.append(ParseWarning(code=code, message=message))
    if not review:
        return document, warnings
    extras = dict(header.extras)
    listed = list(extras.get("review_fields", []))
    extras["review_fields"] = listed + [name for name in review if name not in listed]
    return (
        document.model_copy(
            update={
                "header": header.model_copy(update={"extras": extras}),
                "needs_verification": True,
                "reason_codes": tuple(
                    dict.fromkeys((*document.reason_codes, ReasonCode.LOW_CONFIDENCE_DATA))
                ),
            }
        ),
        warnings,
    )


def party_hints_from_config(engine: Mapping[str, Any]) -> dict[str, Any]:
    """``buyer_entities`` and ``gst_rates`` for :class:`ParserHints` from config ``engine``."""

    entities = tuple(
        BuyerEntityHint(gstin=str(item["gstin"]), name=str(item.get("name") or ""))
        for item in engine.get("buyer_entities", [])
        if isinstance(item, Mapping) and item.get("gstin")
    )
    rates: set[Decimal] = set()
    for slab in engine.get("gst_slabs", []):
        if isinstance(slab, Mapping) and slab.get("rate") is not None:
            try:
                rates.add(Decimal(str(slab["rate"])))
            except ArithmeticError:
                continue
    hints: dict[str, Any] = {"buyer_entities": entities}
    if rates:
        hints["gst_rates"] = tuple(sorted(rates))
    return hints


def guard_result(result: ParseResult, hints: ParserHints) -> ParseResult:
    """Apply :func:`guard_document` to every parsed document."""

    if not result.success or not result.documents:
        return result
    documents: list[Document] = []
    warnings = list(result.warnings)
    for document in result.documents:
        guarded, added = guard_document(document, hints)
        documents.append(guarded)
        warnings.extend(added)
    return result.model_copy(update={"documents": tuple(documents), "warnings": tuple(warnings)})


__all__ = [
    "buyer_gstins",
    "buyer_state",
    "gst_problems",
    "guard_document",
    "guard_result",
    "is_buyer_gstin",
    "is_buyer_name",
    "name_problem",
    "party_hints_from_config",
]
