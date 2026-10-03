"""Boundary and property checks for Phase 4 canonical normalizers."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from string import ascii_letters, digits

import pytest
from hypothesis import given
from hypothesis import strategies as st
from reconbench_core.normalize import (
    canonicalize_document_number,
    canonicalize_po_number,
    classify_line_type,
    financial_year,
    format_indian_number,
    normalize_item_text,
    normalize_uom,
    normalize_vendor_name,
    parse_date,
    parse_decimal,
    validate_gstin,
)
from reconbench_core.schema import LineType


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PO/26-27/0142", "PO2627142"),
        ("PO-26-27-142", "PO2627142"),
        ("po 26 27 142", "PO2627142"),
        ("BM-PO-0142", "BMPO142"),
        ("PO-0000", "PO0"),
    ],
)
def test_po_number_examples(raw: str, expected: str) -> None:
    assert canonicalize_po_number(raw) == expected
    assert canonicalize_document_number(raw) == expected


@given(
    segments=st.lists(
        st.text(alphabet=ascii_letters + digits, min_size=1, max_size=8),
        min_size=1,
        max_size=8,
    ),
    separator=st.sampled_from(("/", "-", " ", ".")),
)
def test_po_canonicalization_is_idempotent(segments: list[str], separator: str) -> None:
    once = canonicalize_po_number(separator.join(segments))
    assert canonicalize_po_number(once) == once


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12,34,567.50", Decimal("1234567.50")),
        ("133,426.85", Decimal("133426.85")),
        ("1,234", Decimal("1234")),
        ("₹ 12,34,567.50", Decimal("1234567.50")),
        ("Rs. 12,34,567.50", Decimal("1234567.50")),
        ("INR 12,34,567.50", Decimal("1234567.50")),
        ("(₹ 12,34,567.50)", Decimal("-1234567.50")),
        ("12,34,567.50-", Decimal("-1234567.50")),
        ("-12,34,567.50", Decimal("-1234567.50")),
    ],
)
def test_indian_decimal_inputs(raw: str, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["123,45,678", "(12.50", "1.2.3", ""])
def test_invalid_or_non_indian_numeric_text_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_decimal(raw)


def test_binary_float_inputs_are_rejected_by_numeric_helpers() -> None:
    with pytest.raises(ValueError):
        parse_decimal(1.25)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        format_indian_number(1.25)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        True,
        Decimal("NaN"),
        Decimal("Infinity"),
        object(),
        " ",  # non-breaking space
        "(10)-",
        "-(10)",
        "1,23,4567",
        "1e3",
    ],
)
def test_decimal_rejects_nonfinite_and_malformed_values(value: object) -> None:
    with pytest.raises(ValueError):
        parse_decimal(value)  # type: ignore[arg-type]


def test_decimal_accepts_integer_decimal_plus_sign_and_nbsp_separator() -> None:
    assert parse_decimal(12) == Decimal("12")
    assert parse_decimal(Decimal("12.50")) == Decimal("12.50")
    assert parse_decimal("+ 1,234.50") == Decimal("1234.50")
    assert parse_decimal(" 12 ") == Decimal("12")


@pytest.mark.parametrize(
    "value",
    [True, Decimal("NaN"), Decimal("-Infinity"), 1.5],
)
def test_indian_formatter_rejects_invalid_inputs(value: object) -> None:
    with pytest.raises(ValueError):
        format_indian_number(value)  # type: ignore[arg-type]


def test_indian_formatter_rejects_negative_decimal_places() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        format_indian_number(Decimal("1"), decimal_places=-1)


@given(
    whole=st.integers(min_value=-(10**15), max_value=10**15),
    places=st.integers(min_value=0, max_value=4),
)
def test_indian_number_format_round_trips(whole: int, places: int) -> None:
    original = Decimal(whole).scaleb(-places)
    rendered = format_indian_number(original)
    assert parse_decimal(rendered) == original


def test_indian_number_formatter_keeps_requested_scale() -> None:
    assert format_indian_number(Decimal("1234567.5"), decimal_places=2) == "12,34,567.50"


def test_gstin_valid_and_invalid_checksum_vectors() -> None:
    valid = validate_gstin("27 AAPFU0939F1ZV")
    invalid = validate_gstin("27AAPFU0939F1ZW")

    assert valid.is_valid
    assert valid.state_code == "27"
    assert valid.pan == "AAPFU0939F"
    assert invalid.format_valid
    assert not invalid.checksum_valid
    assert not validate_gstin("27AAPFU0939F1Z").format_valid


def test_date_formats_and_same_file_majority() -> None:
    assert parse_date("2026-04-03") == date(2026, 4, 3)
    assert parse_date("03/04/2026") == date(2026, 4, 3)
    assert parse_date("28.08.2026") == date(2026, 8, 28)
    assert parse_date("03.04.2026") == date(2026, 4, 3)
    assert parse_date("12-08-2026") == date(2026, 8, 12)
    assert parse_date("12-Sep-26") == date(2026, 9, 12)
    assert parse_date("03/04/26", same_file_dates=("13/04/26", "14/04/26")) == date(2026, 4, 3)
    assert parse_date("03/04/26", same_file_dates=("04/13/26", "04/14/26")) == date(2026, 3, 4)
    assert parse_date("03/04/26") == date(2026, 4, 3)


def test_date_accepts_date_objects_and_datetime_and_rejects_invalid_values() -> None:
    expected = date(2026, 4, 3)
    assert parse_date(expected) is expected
    assert parse_date(datetime(2026, 4, 3, 12, 30)) == expected
    assert parse_date("3-Apr-2026") == expected
    assert parse_date("03-April-26") == expected
    with pytest.raises(ValueError, match="blank"):
        parse_date("  ")
    with pytest.raises(ValueError):
        parse_date("31/02/2026")
    with pytest.raises(ValueError, match="unsupported or invalid"):
        parse_date("2026/04/03")


def test_financial_year_april_boundary() -> None:
    assert financial_year(date(2026, 3, 31)) == "2025-26"
    assert financial_year(date(2026, 4, 1)) == "2026-27"


def test_uom_aliases_and_unknown_flag() -> None:
    assert normalize_uom(" pcs ").canonical == "NOS"
    assert normalize_uom("KGS").canonical == "KG"
    assert normalize_uom("box", {"BOX": "NOS"}).canonical == "NOS"
    unknown = normalize_uom("carton")
    assert unknown.canonical is None
    assert not unknown.is_known


def test_vendor_and_item_text_normalization() -> None:
    assert normalize_vendor_name("M/s. Acer India Pvt. Ltd.") == "acer india"
    item = normalize_item_text("ACER Laptop A515-58, 16GB RAM, 5 inch, 2 kW Silver")
    assert item.description == "acer laptop a515 58 16gb ram 5 inch 2 kw silver"
    assert "16gb" in item.spec_tokens
    assert "a515-58" in item.spec_tokens
    assert "5inch" in item.spec_tokens
    assert "2kw" in item.spec_tokens
    assert "silver" in item.spec_tokens


def test_line_type_uses_configurable_charge_keywords() -> None:
    assert classify_line_type("Freight charges") is LineType.CHARGE
    assert classify_line_type("Packing and handling") is LineType.CHARGE
    assert classify_line_type("Steel bolt") is LineType.GOODS
    assert classify_line_type("Service Fee", ("service fee",)) is LineType.CHARGE
