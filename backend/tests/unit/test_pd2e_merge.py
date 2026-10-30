"""PD-2e ``merge_readings`` (policy ``arb.llm``): the AI leads, OCR is a visible cross-check.

One test per guard of accuracy plan §4.10 that applies (MG-2, MG-3, MG-5, MG-7, MG-11)
plus WI-V1's HSN shape. Synthetic documents only.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from reconbench_core.extraction.merge import (
    CROSS_CHECK_KEY,
    LLM_DISAGREEMENT,
    OCR_READING_KEY,
    hsn_shape_ok,
    merge_readings,
)
from reconbench_core.extraction.vision import (
    AcceptedAnnotation,
    AcceptedCell,
    AcceptedField,
    AcceptedLine,
    Box,
    VisionCheck,
    VisionPageKind,
)
from reconbench_core.schema import (
    Document,
    FieldOrigin,
    FieldSource,
    FieldVerification,
    ReasonCode,
)

BOX = Box(100, 100, 120, 300)


def _field(value: Any, *, source: str = "rule", **extra: Any) -> dict[str, Any]:
    return {"value": value, "confidence": "0.80", "source": source, "evidence": {}, **extra}


def _ocr(
    fields: dict[str, Any] | None = None,
    *,
    lines: tuple[dict[str, Any], ...] = (),
    reasons: tuple[ReasonCode, ...] = (ReasonCode.NO_TEXT_LAYER,),
    **overrides: dict[str, Any],
) -> Document:
    header: dict[str, Any] = {"doc_type": "invoice", "extras": {"source": "ocr"}}
    header.update({name: _field(value) for name, value in (fields or {}).items()})
    header.update(overrides)
    return Document.model_validate(
        {"header": header, "lines": lines, "needs_verification": True, "reason_codes": reasons}
    )


def _ai(name: str, kind: str, text: str, value: Any, *, page: int = 1, **extra: Any) -> Any:
    return AcceptedField(name, kind, text, value, page, BOX, **extra)  # type: ignore[arg-type]


def _check(
    *fields: AcceptedField,
    lines: tuple[AcceptedLine, ...] = (),
    pages: tuple[VisionPageKind, ...] = (),
    annotations: tuple[AcceptedAnnotation, ...] = (),
) -> VisionCheck:
    return VisionCheck(
        doc_kind="invoice",
        fields=fields,
        lines=lines,
        annotations=annotations,
        page_kinds=pages,
    )


def _line(index: int, amount: str, *, hsn: str | None = None, qty: str = "1") -> AcceptedLine:
    cells = [
        AcceptedCell("description", f"Synthetic item {index}", f"Synthetic item {index}"),
        AcceptedCell("quantity", qty, Decimal(qty)),
        AcceptedCell("amount", amount, Decimal(amount)),
    ]
    if hsn is not None:
        cells.append(AcceptedCell("hsn_sac", hsn, hsn))
    return AcceptedLine(index, 1, BOX, tuple(cells))


def _cross(document: Document, field: str) -> dict[str, Any]:
    return next(item for item in document.header.extras[CROSS_CHECK_KEY] if item["field"] == field)


def test_agreeing_values_become_ai_values_below_the_review_floor() -> None:
    result = merge_readings(
        _ocr({"doc_no": "INV-000123"}), _check(_ai("doc_no", "doc_no", "INV-000123", "INV-000123"))
    )
    doc_no = result.document.header.doc_no
    assert doc_no is not None and doc_no.value == "INV-000123"
    # MG-2: origin AI, unverified, capped below 0.70; the document needs a person (MG-4/MG-10).
    assert (doc_no.origin, doc_no.source, doc_no.verification) == (
        FieldOrigin.AI,
        FieldSource.AI,
        FieldVerification.NONE,
    )
    assert doc_no.confidence is not None and doc_no.confidence < Decimal("0.70")
    assert result.document.needs_verification
    assert _cross(result.document, "header:doc_no")["outcome"] == "agree"
    assert result.usable and result.policy == "arb.llm"


def test_a_disagreement_keeps_the_ai_value_and_shows_the_ocr_value_beside_it() -> None:
    result = merge_readings(
        _ocr({"grand_total": Decimal("1180.00")}),
        _check(_ai("grand_total", "money", "1,108.00", Decimal("1108.00"))),
    )
    total = result.document.header.grand_total
    assert total is not None and total.value == Decimal("1108.00")
    assert total.origin is FieldOrigin.AI
    cross = _cross(result.document, "header:grand_total")
    assert cross == {
        "field": "header:grand_total",
        "outcome": "disagree",
        "ai": "1108.00",
        "ocr": "1180.00",
        "reason": LLM_DISAGREEMENT,
    }
    assert [item.field for item in result.disagreements] == ["header:grand_total"]
    assert result.document.header.extras[OCR_READING_KEY]["grand_total"] == "1180.00"


def test_a_value_the_ai_did_not_read_stays_the_ocr_value() -> None:
    result = merge_readings(
        _ocr({"doc_no": "INV-9", "grand_total": Decimal("10.00")}),
        _check(_ai("doc_no", "doc_no", "INV-9", "INV-9")),
    )
    total = result.document.header.grand_total
    assert total is not None and total.value == Decimal("10.00")
    assert total.origin is FieldOrigin.OCR and total.source is FieldSource.RULE
    assert _cross(result.document, "header:grand_total")["outcome"] == "ocr_kept"


@pytest.mark.parametrize(
    "held",
    [
        {"source": "manual"},
        {"verification": "user", "verified_by": "synthetic-user"},
    ],
)
def test_a_value_a_person_typed_or_verified_is_never_replaced(held: dict[str, Any]) -> None:
    """MG-7."""

    field = {**_field(Decimal("1180.00")), **held}
    result = merge_readings(
        _ocr(grand_total=field), _check(_ai("grand_total", "money", "1108", Decimal("1108")))
    )
    total = result.document.header.grand_total
    assert total is not None and total.value == Decimal("1180.00")
    assert total.origin is not FieldOrigin.AI
    assert _cross(result.document, "header:grand_total")["outcome"] == "person_kept"


@pytest.mark.parametrize(
    ("check_failed", "reason"),
    [(("page_conflict",), "page_conflict"), (("date_window",), "check_failed:date_window")],
)
def test_a_value_that_failed_a_check_is_never_merged(
    check_failed: tuple[str, ...], reason: str
) -> None:
    """MG-3 and D63: kept as a suggestion only."""

    result = merge_readings(
        _ocr({"doc_date": date(2026, 4, 1)}),
        _check(_ai("doc_date", "date", "01/05/2025", date(2025, 5, 1), check_failed=check_failed)),
    )
    doc_date = result.document.header.doc_date
    assert doc_date is not None and doc_date.value == date(2026, 4, 1)
    cross = _cross(result.document, "header:doc_date")
    assert (cross["outcome"], cross["reason"]) == ("ocr_kept", reason)
    assert not result.usable


def test_a_value_from_a_page_of_another_document_is_never_taken() -> None:
    """MG-11: the S05 page-3 case (taxable total read from an e-way bill page)."""

    result = merge_readings(
        _ocr({"taxable_total": Decimal("1000.00")}),
        _check(
            _ai("taxable_total", "money", "9,999.00", Decimal("9999.00"), page=2),
            pages=(VisionPageKind(1, "invoice"), VisionPageKind(2, "eway_bill")),
        ),
    )
    taxable = result.document.header.taxable_total
    assert taxable is not None and taxable.value == Decimal("1000.00")
    assert _cross(result.document, "header:taxable_total")["reason"] == "other_document_page"
    assert result.left_out == {"other_document_page": 1}


def test_references_gathered_from_several_pages_are_flagged() -> None:
    """MG-5."""

    result = merge_readings(
        _ocr({"po_refs": ("PO-1",)}),
        _check(
            _ai("po_ref", "po_ref", "PO-1", "PO-1", page=1),
            _ai("po_ref", "po_ref", "PO-2", "PO-2", page=2),
        ),
    )
    cross = _cross(result.document, "header:po_refs")
    assert cross["outcome"] == "disagree"
    assert "refs_from_several_pages" in cross["reason"]


@pytest.mark.parametrize(
    ("text", "ok"),
    [("8471", True), ("847130", True), ("84713010", True), ("998314", True), ("84 71", True)],
)
def test_hsn_shapes_that_pass(text: str, ok: bool) -> None:
    assert hsn_shape_ok(text) is ok


@pytest.mark.parametrize("text", ["847", "84713", "8471301", "12AB", "HSN", ""])
def test_hsn_shapes_that_fail(text: str) -> None:
    assert not hsn_shape_ok(text)


def test_ai_lines_lead_and_a_bad_hsn_is_left_out() -> None:
    ocr_lines = (
        {"line_no": 1, "amount": _field(Decimal("100.00")), "quantity": _field(Decimal("1"))},
        {"line_no": 2, "amount": _field(Decimal("50.00")), "quantity": _field(Decimal("1"))},
    )
    result = merge_readings(
        _ocr({"doc_no": "INV-1"}, lines=ocr_lines),
        _check(lines=(_line(1, "100.00", hsn="12AB"), _line(2, "50.00", hsn="8471", qty="2"))),
    )
    first, second = result.document.lines
    assert first.hsn_sac is None and result.left_out == {"hsn_shape": 1}
    assert second.hsn_sac is not None and second.hsn_sac.value == "8471"
    assert first.amount is not None and first.amount.origin is FieldOrigin.AI
    crosses = {item.field: item for item in result.cross_checks}
    assert crosses["line:1:amount"].outcome == "agree"
    assert crosses["line:2:amount"].outcome == "agree"
    assert crosses["line:2:quantity"].outcome == "disagree"
    assert second.extras["ocr_line_no"] == 2


def test_a_line_only_the_rules_read_is_listed_for_the_person() -> None:
    ocr_lines = (
        {"line_no": 1, "amount": _field(Decimal("100.00"))},
        {"line_no": 2, "amount": _field(Decimal("7.00"))},
    )
    result = merge_readings(
        _ocr({"doc_no": "INV-1"}, lines=ocr_lines), _check(lines=(_line(1, "100.00"),))
    )
    assert len(result.document.lines) == 1
    [only] = [item for item in result.cross_checks if item.outcome == "ocr_only_line"]
    assert (only.field, only.ocr) == ("line:2:*", "7.00")


def test_without_ai_lines_the_ocr_lines_stay() -> None:
    ocr_lines = ({"line_no": 1, "amount": _field(Decimal("100.00"))},)
    result = merge_readings(
        _ocr({"doc_no": "INV-1"}, lines=ocr_lines),
        _check(_ai("doc_no", "doc_no", "INV-1", "INV-1")),
    )
    [line] = result.document.lines
    assert line.amount is not None and line.amount.value == Decimal("100.00")
    assert line.amount.origin is FieldOrigin.OCR


def test_an_empty_answer_is_not_usable_and_changes_no_value() -> None:
    ocr = _ocr({"doc_no": "INV-1"})
    result = merge_readings(ocr, _check())
    assert not result.usable
    doc_no = result.document.header.doc_no
    assert doc_no is not None and doc_no.value == "INV-1" and doc_no.origin is FieldOrigin.OCR
    # Nothing was read, so a scan with no text layer stays unreadable.
    assert ReasonCode.NO_TEXT_LAYER in result.document.reason_codes


def test_a_read_scan_is_no_longer_unreadable_but_still_needs_a_person() -> None:
    result = merge_readings(_ocr(), _check(_ai("doc_no", "doc_no", "INV-7", "INV-7")))
    assert ReasonCode.NO_TEXT_LAYER not in result.document.reason_codes
    assert ReasonCode.LOW_CONFIDENCE_DATA in result.document.reason_codes
    assert result.document.needs_verification


def test_handwritten_references_stay_hints_in_extras() -> None:
    """MG-9 as changed by PD-2h: typed refs are carried for link suggestions only."""

    note = AcceptedAnnotation("handwritten", "PO 01-100", "po_number", 1, BOX)
    result = merge_readings(
        _ocr({"doc_no": "INV-1"}),
        _check(_ai("doc_no", "doc_no", "INV-1", "INV-1"), annotations=(note,)),
    )
    extras = result.document.header.extras
    assert extras.get("annotation_refs") == ["PO 01-100"]
    assert result.document.header.po_refs is None


def test_the_merge_is_deterministic() -> None:
    ocr = _ocr({"doc_no": "INV-1", "grand_total": Decimal("1.00")})
    check = _check(
        _ai("doc_no", "doc_no", "INV-1", "INV-1"),
        _ai("grand_total", "money", "2.00", Decimal("2.00")),
    )
    assert merge_readings(ocr, check) == merge_readings(ocr, check)


def test_empty_ocr_list_and_ai_value_is_ai_only_not_disagreement() -> None:
    """KI-005: an empty OCR value is treated like a missing one."""
    result = merge_readings(
        _ocr({"po_refs": ()}),
        _check(_ai("po_ref", "po_ref", "PO-100", "PO-100")),
    )
    refs = result.document.header.po_refs
    assert refs is not None and refs.value == ("PO-100",)
    assert refs.origin is FieldOrigin.AI
    cross = _cross(result.document, "header:po_refs")
    assert cross == {
        "field": "header:po_refs",
        "outcome": "ai_only",
        "ai": "PO-100",
        "ocr": None,
        "reason": None,
    }
    assert not any(item.field == "header:po_refs" for item in result.disagreements)


def test_non_empty_different_ocr_list_is_still_disagree() -> None:
    result = merge_readings(
        _ocr({"po_refs": ("PO-999",)}),
        _check(_ai("po_ref", "po_ref", "PO-100", "PO-100")),
    )
    refs = result.document.header.po_refs
    assert refs is not None and refs.value == ("PO-100",)
    assert refs.origin is FieldOrigin.AI
    cross = _cross(result.document, "header:po_refs")
    assert cross["outcome"] == "disagree"
    assert cross["reason"] == LLM_DISAGREEMENT
    assert [item.field for item in result.disagreements] == ["header:po_refs"]


def test_person_held_empty_value_is_still_person_kept() -> None:
    held_empty = {**_field(()), "source": "manual"}
    result = merge_readings(
        _ocr(po_refs=held_empty),
        _check(_ai("po_ref", "po_ref", "PO-100", "PO-100")),
    )
    refs = result.document.header.po_refs
    assert refs is not None and refs.value == ()
    assert refs.source is FieldSource.MANUAL
    cross = _cross(result.document, "header:po_refs")
    assert cross["outcome"] == "person_kept"
    assert cross["ai"] == "PO-100"
    assert cross["ocr"] == ""
