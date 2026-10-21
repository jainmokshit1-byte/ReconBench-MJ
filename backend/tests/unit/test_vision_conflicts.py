"""D77: a single-valued field read with different values on different pages.

One page per call returns the same field from several pages; ``check_reading`` keeps
one value marked ``page_conflict`` (a visible suggestion, never auto-accepted or
merged) instead of dropping all of them. Synthetic values only.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from reconbench_core.extraction.consensus import FieldKey, vision_readings
from reconbench_core.extraction.vision import (
    SentPage,
    VisionField,
    VisionReading,
    check_reading,
)
from reconbench_core.schema import Document

PAGES = {1: SentPage(1), 2: SentPage(2), 3: SentPage(3)}
BOX = (100, 100, 120, 300)


def _field(name: str, text: str, page: int) -> VisionField:
    return VisionField(name, text, page, BOX)


def _check(*fields: VisionField, ocr: Document | None = None) -> Any:
    return check_reading(VisionReading("invoice", fields=fields), pages=PAGES, ocr=ocr)


def _ocr(**header: str) -> Document:
    return Document.model_validate(
        {
            "header": {
                "doc_type": "invoice",
                **{
                    name: {"value": value, "confidence": "0.98", "source": "rule", "evidence": {}}
                    for name, value in header.items()
                },
            }
        }
    )


def _kept(check: Any, name: str) -> list[tuple[Any, int, tuple[str, ...]]]:
    return [
        (item.value, item.page, item.check_failed) for item in check.fields if item.name == name
    ]


def test_identity_field_keeps_the_first_page_value_marked() -> None:
    check = _check(_field("doc_no", "SYN-INV-0001", 1), _field("doc_no", "SYN-INV-0007", 2))
    assert _kept(check, "doc_no") == [("SYN-INV-0001", 1, ("page_conflict",))]
    assert check.dropped["conflict"] == 1
    assert check.check_failed["page_conflict"] == 1


def test_a_total_keeps_the_last_page_value() -> None:
    check = _check(
        _field("taxable_total", "400.00", 1),
        _field("taxable_total", "250.00", 2),
        _field("taxable_total", "1,000.00", 3),
    )
    assert _kept(check, "taxable_total") == [(Decimal("1000.00"), 3, ("page_conflict",))]
    assert check.dropped["conflict"] == 2


def test_the_value_the_rules_reader_also_read_wins_over_page_order() -> None:
    check = _check(
        _field("doc_no", "SYN-INV-0001", 1),
        _field("doc_no", "SYN-INV-0007", 2),
        ocr=_ocr(doc_no="SYN/INV/0007"),
    )
    assert _kept(check, "doc_no") == [("SYN-INV-0007", 2, ("page_conflict",))]


def test_ocr_agreement_also_settles_a_date_and_a_same_page_conflict() -> None:
    check = _check(
        _field("doc_date", "01-04-2026", 1),
        _field("doc_date", "03-04-2026", 1),
        ocr=_ocr(doc_date="2026-04-03"),
    )
    assert _kept(check, "doc_date") == [(date(2026, 4, 3), 1, ("page_conflict",))]


def test_a_same_page_conflict_without_ocr_is_still_dropped() -> None:
    check = _check(_field("doc_no", "SYN-INV-0001", 1), _field("doc_no", "SYN-INV-0007", 1))
    assert _kept(check, "doc_no") == []
    assert check.dropped["conflict"] == 2
    assert "page_conflict" not in check.check_failed


def test_two_values_on_the_chosen_page_are_still_dropped() -> None:
    check = _check(
        _field("doc_no", "SYN-INV-0001", 1),
        _field("doc_no", "SYN-INV-0002", 1),
        _field("doc_no", "SYN-INV-0007", 2),
    )
    assert _kept(check, "doc_no") == []
    assert check.dropped["conflict"] == 3


def test_the_chosen_value_repeated_on_another_page_is_kept_once() -> None:
    check = _check(
        _field("doc_no", "SYN-INV-0001", 1),
        _field("doc_no", "SYN-INV-0007", 2),
        _field("doc_no", "SYN-INV-0001", 3),
    )
    assert _kept(check, "doc_no") == [("SYN-INV-0001", 1, ("page_conflict",))]
    assert check.dropped == {"conflict": 1, "duplicate": 1}


def test_no_conflict_means_no_mark_and_multi_valued_refs_are_untouched() -> None:
    check = _check(
        _field("doc_no", "SYN-INV-0001", 1),
        _field("doc_no", "SYN-INV-0001", 2),
        _field("po_ref", "SYN-PO-0001", 1),
        _field("po_ref", "SYN-PO-0002", 2),
    )
    assert _kept(check, "doc_no") == [("SYN-INV-0001", 1, ())]
    assert [item.value for item in check.fields if item.name == "po_ref"] == [
        "SYN-PO-0001",
        "SYN-PO-0002",
    ]
    assert "page_conflict" not in check.check_failed and "conflict" not in check.dropped


def test_a_resolved_value_keeps_its_other_check_marks() -> None:
    # Totals that do not add up keep totals_arithmetic next to page_conflict.
    check = _check(
        _field("taxable_total", "900.00", 1),
        _field("taxable_total", "1,000.00", 2),
        _field("igst_total", "180.00", 2),
        _field("grand_total", "1,500.00", 2),
    )
    [(_value, _page, marks)] = _kept(check, "taxable_total")
    assert marks == ("totals_arithmetic", "page_conflict")


def test_consensus_never_uses_a_resolved_value() -> None:
    check = _check(
        _field("doc_no", "SYN-INV-0001", 1),
        _field("doc_no", "SYN-INV-0007", 2),
        _field("doc_date", "01-04-2026", 1),
    )
    readings, _unpaired = vision_readings(_ocr(doc_no="SYN-INV-0001"), check)
    assert FieldKey("header", "doc_no") not in readings
    assert FieldKey("header", "doc_date") in readings
