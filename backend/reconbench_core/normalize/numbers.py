"""Exact decimal parsing and Indian digit grouping."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_CURRENCY_PREFIX = re.compile(r"^(?:₹|INR(?=\s|[\d(])|RS\.?(?=\s|[\d(]))\s*", re.IGNORECASE)
_INDIAN_GROUPED_INTEGER = re.compile(r"\d{1,2}(?:,\d{2})*,\d{3}\Z")
_WESTERN_GROUPED_INTEGER = re.compile(r"\d{1,3}(?:,\d{3})+\Z")
_PLAIN_INTEGER = re.compile(r"\d+\Z")
_NUMBER = re.compile(r"(?P<integer>[\d,]+)(?:\.(?P<fraction>\d+))?\Z")


def parse_decimal(value: str | int | Decimal) -> Decimal:
    """Parse rupee/quantity text to Decimal without passing through binary float."""

    if isinstance(value, bool):
        raise ValueError("boolean is not a valid number")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("number must be finite")
        return value
    if isinstance(value, int):
        return Decimal(value)
    if not isinstance(value, str):
        raise ValueError("number must be Decimal, integer, or text")

    text = value.strip().replace("\u00a0", " ")
    if not text:
        raise ValueError("number is blank")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    elif text.startswith("(") or text.endswith(")"):
        raise ValueError("unbalanced parentheses in number")

    text = _CURRENCY_PREFIX.sub("", text, count=1).strip()
    if text.endswith("-"):
        if negative or text.startswith("-"):
            raise ValueError("number has more than one negative marker")
        negative = True
        text = text[:-1].strip()
    elif text.startswith("-"):
        if negative:
            raise ValueError("number has more than one negative marker")
        negative = True
        text = text[1:].strip()
    elif text.startswith("+"):
        text = text[1:].strip()

    match = _NUMBER.fullmatch(text)
    if match is None:
        raise ValueError(f"invalid numeric value: {value!r}")

    integer = match.group("integer")
    if "," in integer:
        if not (
            _INDIAN_GROUPED_INTEGER.fullmatch(integer)
            or _WESTERN_GROUPED_INTEGER.fullmatch(integer)
        ):
            raise ValueError(f"invalid digit grouping: {value!r}")
        integer = integer.replace(",", "")
    elif not _PLAIN_INTEGER.fullmatch(integer):
        raise ValueError(f"invalid integer part: {value!r}")

    fraction = match.group("fraction")
    normalized = integer if fraction is None else f"{integer}.{fraction}"
    try:
        result = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric value: {value!r}") from exc
    return -result if negative else result


def format_indian_number(value: Decimal | int, *, decimal_places: int | None = None) -> str:
    """Format a finite Decimal with Indian grouping, preserving its scale by default."""

    if isinstance(value, bool):
        raise ValueError("boolean is not a valid number")
    if not isinstance(value, (int, Decimal)):
        raise ValueError("number must be Decimal or integer")
    number = Decimal(value) if isinstance(value, int) else value
    if not number.is_finite():
        raise ValueError("number must be finite")
    if decimal_places is not None:
        if decimal_places < 0:
            raise ValueError("decimal_places must be non-negative")
        quantum = Decimal(1).scaleb(-decimal_places)
        number = number.quantize(quantum)

    rendered = format(number, "f")
    sign = ""
    if rendered.startswith("-"):
        sign, rendered = "-", rendered[1:]
    integer, separator, fraction = rendered.partition(".")

    if len(integer) > 3:
        last_three = integer[-3:]
        prefix = integer[:-3]
        groups: list[str] = []
        while prefix:
            groups.append(prefix[-2:])
            prefix = prefix[:-2]
        integer = ",".join((*reversed(groups), last_three))

    suffix = f".{fraction}" if separator else ""
    return f"{sign}{integer}{suffix}"
