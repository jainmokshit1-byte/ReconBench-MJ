"""Labeller A versus labeller B agreement, counts only (accuracy plan WI-D5, step 4).

Reads two Tier H label sheets (CSV with the columns ``invoice``, ``field``, ``status``,
``value``; ``status`` is ``present``, ``not_present`` or ``illegible``) and prints how
many fields agree, disagree or are illegible, per field and for the critical fields. It
prints counts only: never a value, and invoice ids only as counts. Exact match after
trimming; money is compared as a number with two decimals, GSTINs and references in
upper case, a reference list as a sorted set. No look-alike folding: ``O`` is not ``0``.

    python scripts/truth_agreement.py a.csv b.csv [--min-critical 0.95]

Exit code 1 when ``--min-critical`` is given and critical-field agreement is below it.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CRITICAL_FIELDS = frozenset(
    {"doc_no", "doc_date", "vendor_gstin", "grand_total", "po_refs", "taxable_total"}
)
MONEY_FIELDS = frozenset({"taxable_total", "cgst_total", "sgst_total", "igst_total", "grand_total"})
UPPER_FIELDS = frozenset({"doc_no", "vendor_gstin", "buyer_gstin"})
STATUSES = frozenset({"present", "not_present", "illegible"})


def _normal(field: str, status: str, value: str) -> str:
    if status != "present":
        return f"<{status}>"
    text = value.strip()
    if field in MONEY_FIELDS:
        try:
            return str(Decimal(text.replace(",", "")).quantize(Decimal("0.01")))
        except InvalidOperation:
            return text
    if field == "po_refs":
        return ";".join(sorted({part.strip().upper() for part in text.split(";") if part.strip()}))
    if field in UPPER_FIELDS:
        return " ".join(text.upper().split())
    return " ".join(text.split())


def read_sheet(rows: Iterable[Mapping[str, str]]) -> dict[tuple[str, str], tuple[str, str]]:
    sheet: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        invoice = (row.get("invoice") or "").strip()
        field = (row.get("field") or "").strip()
        status = (row.get("status") or "").strip()
        if not invoice or not field:
            continue
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r} for field {field}")
        key = (invoice, field)
        if key in sheet:
            raise ValueError(f"duplicate label for field {field}")
        sheet[key] = (status, row.get("value") or "")
    return sheet


def compare(
    a: Mapping[tuple[str, str], tuple[str, str]], b: Mapping[tuple[str, str], tuple[str, str]]
) -> dict[str, Any]:
    both = sorted(set(a) & set(b))
    per_field: dict[str, Counter[str]] = {}
    for invoice, field in both:
        status_a, value_a = a[(invoice, field)]
        status_b, value_b = b[(invoice, field)]
        counter = per_field.setdefault(field, Counter())
        if "illegible" in (status_a, status_b):
            counter["illegible"] += 1
        elif _normal(field, status_a, value_a) == _normal(field, status_b, value_b):
            counter["agree"] += 1
        else:
            counter["disagree"] += 1
    critical = Counter[str]()
    for field, counter in per_field.items():
        if field in CRITICAL_FIELDS:
            critical.update(counter)
    scored = critical["agree"] + critical["disagree"]
    return {
        "invoices_in_both": len({invoice for invoice, _ in both}),
        "fields_compared": len(both),
        "only_in_a": len(set(a) - set(b)),
        "only_in_b": len(set(b) - set(a)),
        "per_field": {field: dict(per_field[field]) for field in sorted(per_field)},
        "critical": {
            "agree": critical["agree"],
            "disagree": critical["disagree"],
            "illegible": critical["illegible"],
            "agreement": (f"{critical['agree']}/{scored}" if scored else "0/0"),
            "rate": (critical["agree"] / scored) if scored else None,
        },
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sheet_a", type=Path)
    parser.add_argument("sheet_b", type=Path)
    parser.add_argument("--min-critical", type=float, default=None)
    args = parser.parse_args(argv[1:])
    try:
        with args.sheet_a.open(encoding="utf-8", newline="") as handle_a:
            sheet_a = read_sheet(csv.DictReader(handle_a))
        with args.sheet_b.open(encoding="utf-8", newline="") as handle_b:
            sheet_b = read_sheet(csv.DictReader(handle_b))
    except (OSError, ValueError) as exc:
        print(f"cannot compare: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    report = compare(sheet_a, sheet_b)
    print(json.dumps(report, indent=2, sort_keys=True))
    rate = report["critical"]["rate"]
    if args.min_critical is not None and (rate is None or rate < args.min_critical):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
