"""Indian date parsing and financial-year derivation."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum


class DateOrder(StrEnum):
    DAY_FIRST = "day_first"
    MONTH_FIRST = "month_first"


_SLASH_DATE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\s*$")
_DOT_DATE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\s*$")
_HYPHEN_DATE = re.compile(r"^\s*(\d{1,2})-(\d{1,2})-(\d{2}|\d{4})\s*$")


def _date_order_from_same_file(values: Iterable[str]) -> DateOrder | None:
    votes = {DateOrder.DAY_FIRST: 0, DateOrder.MONTH_FIRST: 0}
    for value in values:
        match = _SLASH_DATE.fullmatch(value)
        if match is None:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12 and second <= 12:
            votes[DateOrder.DAY_FIRST] += 1
        elif second > 12 and first <= 12:
            votes[DateOrder.MONTH_FIRST] += 1

    if votes[DateOrder.DAY_FIRST] == votes[DateOrder.MONTH_FIRST]:
        return None
    return max(votes, key=votes.__getitem__)


def parse_date(value: str | date, *, same_file_dates: Iterable[str] = ()) -> date:
    """Parse ISO, Indian day-first, or named-month dates deterministically.

    Ambiguous slash dates follow the majority format among unambiguous slash
    dates in the same source file. With no majority, Indian day-first wins.
    """

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = value.strip()
    if not text:
        raise ValueError("date is blank")

    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    dot_match = _DOT_DATE.fullmatch(text)
    if dot_match is not None:
        day, month = int(dot_match.group(1)), int(dot_match.group(2))
        year = dot_match.group(3)
        fmt = "%d.%m.%Y" if len(year) == 4 else "%d.%m.%y"
        return datetime.strptime(f"{day:02d}.{month:02d}.{year}", fmt).date()

    hyphen_match = _HYPHEN_DATE.fullmatch(text)
    if hyphen_match is not None:
        day, month = int(hyphen_match.group(1)), int(hyphen_match.group(2))
        year = hyphen_match.group(3)
        fmt = "%d-%m-%Y" if len(year) == 4 else "%d-%m-%y"
        return datetime.strptime(f"{day:02d}-{month:02d}-{year}", fmt).date()

    slash_match = _SLASH_DATE.fullmatch(text)
    if slash_match is not None:
        first, second = int(slash_match.group(1)), int(slash_match.group(2))
        year = slash_match.group(3)
        ambiguous = first <= 12 and second <= 12 and first != second
        order = _date_order_from_same_file(same_file_dates) if ambiguous else None
        if order is None:
            order = DateOrder.DAY_FIRST
        day, month = (first, second) if order is DateOrder.DAY_FIRST else (second, first)
        fmt = "%d/%m/%Y" if len(year) == 4 else "%d/%m/%y"
        return datetime.strptime(f"{day:02d}/{month:02d}/{year}", fmt).date()

    for pattern in ("%d-%b-%y", "%d-%b-%Y", "%d-%B-%y", "%d-%B-%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported or invalid date: {value!r}")


def financial_year(value: date) -> str:
    """Return the Indian financial year label, for example ``2026-27``."""

    start_year = value.year if value.month >= 4 else value.year - 1
    return f"{start_year:04d}-{(start_year + 1) % 100:02d}"
