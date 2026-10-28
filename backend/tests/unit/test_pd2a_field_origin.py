"""PD-2a: the stored origin of a value follows its source and the extraction method."""

from __future__ import annotations

import itertools

import pytest
from reconbench_app.ingestion import _field_origin
from reconbench_core.schema import (
    ExtractionMethod,
    FieldEvidence,
    FieldOrigin,
    FieldSource,
    FieldValue,
)

_ORIGIN_BY_METHOD = {
    ExtractionMethod.XML_PROFILE: FieldOrigin.ERP_IMPORT,
    ExtractionMethod.CSV_MAPPING: FieldOrigin.ERP_IMPORT,
    ExtractionMethod.PDF_TEXT: FieldOrigin.OCR,
    ExtractionMethod.PDF_OCR: FieldOrigin.OCR,
    ExtractionMethod.AI_FALLBACK: FieldOrigin.AI,
    ExtractionMethod.AI_FIRST: FieldOrigin.AI,
    ExtractionMethod.MANUAL: FieldOrigin.MANUAL,
    ExtractionMethod.SHELL: None,
}


def _value(source: FieldSource, origin: FieldOrigin | None) -> FieldValue[str]:
    return FieldValue(value="X", source=source, evidence=FieldEvidence(), origin=origin)


def test_every_extraction_method_has_an_expected_origin() -> None:
    assert set(_ORIGIN_BY_METHOD) == set(ExtractionMethod)


@pytest.mark.parametrize("method", list(ExtractionMethod))
def test_a_rule_value_without_its_own_origin_takes_the_method_origin(
    method: ExtractionMethod,
) -> None:
    assert _field_origin(_value(FieldSource.RULE, None), method) is _ORIGIN_BY_METHOD[method]


@pytest.mark.parametrize(
    ("method", "origin"), list(itertools.product(ExtractionMethod, [*FieldOrigin, None]))
)
def test_a_value_read_by_ai_is_always_origin_ai(
    method: ExtractionMethod, origin: FieldOrigin | None
) -> None:
    assert _field_origin(_value(FieldSource.AI, origin), method) is FieldOrigin.AI


@pytest.mark.parametrize(
    ("method", "origin"), list(itertools.product(ExtractionMethod, FieldOrigin))
)
def test_a_rule_or_manual_value_keeps_its_own_origin(
    method: ExtractionMethod, origin: FieldOrigin
) -> None:
    assert _field_origin(_value(FieldSource.RULE, origin), method) is origin
    assert _field_origin(_value(FieldSource.MANUAL, origin), method) is origin


@pytest.mark.parametrize("method", list(ExtractionMethod))
def test_a_manual_value_without_its_own_origin_is_manual(method: ExtractionMethod) -> None:
    assert _field_origin(_value(FieldSource.MANUAL, None), method) is FieldOrigin.MANUAL
