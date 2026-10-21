"""The D42 document-date window, shared by the consensus validators and the vision checks."""

from __future__ import annotations

from datetime import date, timedelta

DEFAULT_DATE_PAST_DAYS = 540
DEFAULT_DATE_FUTURE_DAYS = 1


def date_in_window(
    value: object,
    as_of: date | None,
    *,
    past_days: int = DEFAULT_DATE_PAST_DAYS,
    future_days: int = DEFAULT_DATE_FUTURE_DAYS,
) -> bool:
    """``value`` is a date no more than ``past_days`` before and ``future_days`` after ``as_of``.

    Without ``as_of`` nothing can be in the window.
    """

    return (
        isinstance(value, date)
        and as_of is not None
        and as_of - timedelta(days=past_days) <= value <= as_of + timedelta(days=future_days)
    )


__all__ = ["DEFAULT_DATE_FUTURE_DAYS", "DEFAULT_DATE_PAST_DAYS", "date_in_window"]
