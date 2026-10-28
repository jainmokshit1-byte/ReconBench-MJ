"""PD-4d: the Verify request and the rule for "AI and OCR agree" (Q8)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from reconbench_app.api.ingestion import VerifyAiValuesBody
from reconbench_app.ingestion import _values_agree


def test_a_request_names_values_or_asks_for_all_agreeing_never_both_or_neither() -> None:
    assert VerifyAiValuesBody.model_validate({"all_agreeing": True}).all_agreeing
    assert VerifyAiValuesBody.model_validate({"values": [{"field_name": "doc_no"}]}).values
    for body in ({}, {"values": []}, {"values": [{"field_name": "doc_no"}], "all_agreeing": True}):
        with pytest.raises(ValidationError):
            VerifyAiValuesBody.model_validate(body)
    with pytest.raises(ValidationError):
        VerifyAiValuesBody.model_validate({"all_agreeing": True, "extra": 1})


@pytest.mark.parametrize(
    ("ai", "ocr"),
    [
        ("1200.00", "1200"),
        ("2", "2.000"),
        ("INV-1", "INV-1"),
        ("Synthetic  Supplier", "synthetic supplier"),
        ("2026-09-05", "2026-09-05"),
    ],
)
def test_the_same_value_written_differently_agrees(ai: str, ocr: str) -> None:
    assert _values_agree(ai, ocr)


@pytest.mark.parametrize(
    ("ai", "ocr"),
    [
        ("1200.00", "1200.01"),
        ("1200", "12000"),
        ("INV-1", "INV-7"),
        ("1,200", "1200"),
        ("2026-09-05", "2026-05-09"),
        ("1200", None),
        (None, "1200"),
        (None, None),
        ("NaN", "NaN"),
    ],
)
def test_a_different_or_missing_value_never_agrees(ai: str | None, ocr: str | None) -> None:
    assert not _values_agree(ai, ocr)
