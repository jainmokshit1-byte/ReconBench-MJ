"""Measure PDF extraction accuracy against a hand-labelled truth file.

Usage (inside an image that has OCRmyPDF/Tesseract, e.g. the worker image):
    python scripts/measure_extraction.py --truth TRUTH.json --pdfs DIR [--doc-type invoice]

The truth file lists invoices with header fields and lines (see
``work/extraction-truth/invoices.json`` for the format). Each truth entry names
file-name prefixes; every matching PDF is scored separately. Output is summary
metrics only; document text is never printed.

Scoring:
- header field: correct / wrong / missing per field (exact after normalisation);
- lines: a truth line counts as found when an extracted line has the same amount
  (and quantity when both are present); precision counts extracted lines that
  match no truth line;
- "auto-safe": a document is safe when it is either fully correct or flagged for
  verification. An unflagged document with any wrong critical value is a
  **false-safe** and must stay at zero.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from reconbench_app.pdf_sandbox import parse_pdf_isolated  # noqa: E402
from reconbench_core.parsers.types import ParserHints  # noqa: E402
from reconbench_core.schema import DocType, FileFormat  # noqa: E402

HEADER_FIELDS = (
    "doc_no",
    "doc_date",
    "vendor_gstin",
    "buyer_gstin",
    "taxable_total",
    "cgst",
    "sgst",
    "igst",
    "grand_total",
)
CRITICAL = ("doc_no", "doc_date", "vendor_gstin", "grand_total")
MONEY = {"taxable_total", "cgst", "sgst", "igst", "grand_total"}


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _normal(field_name: str, value: Any) -> Any:
    if value is None:
        return None
    if field_name in MONEY:
        return _decimal(value)
    if isinstance(value, date):
        return value.isoformat()
    return "".join(str(value).split()).upper()


@dataclass
class Score:
    fields: Counter[str] = field(default_factory=Counter)
    per_field: dict[str, Counter[str]] = field(default_factory=dict)
    lines_truth: int = 0
    lines_found: int = 0
    lines_extracted: int = 0
    lines_spurious: int = 0
    docs: int = 0
    docs_flagged: int = 0
    false_safe: int = 0
    seconds: float = 0.0

    def field_result(self, name: str, outcome: str) -> None:
        self.fields[outcome] += 1
        self.per_field.setdefault(name, Counter())[outcome] += 1


def _score_document(score: Score, truth: dict[str, Any], document: Any) -> dict[str, Any]:
    header = document.header
    wrong_critical = False
    detail: dict[str, str] = {}
    for name in HEADER_FIELDS:
        expected = _normal(name, truth["header"].get(name))
        extracted = getattr(header, name, None)
        actual = _normal(name, extracted.value if extracted is not None else None)
        if expected is None and actual is None:
            continue
        if expected is None:
            outcome = "unexpected"
        elif actual is None:
            outcome = "missing"
        elif actual == expected:
            outcome = "correct"
        else:
            outcome = "wrong"
            if name in CRITICAL:
                wrong_critical = True
        score.field_result(name, outcome)
        detail[name] = outcome

    truth_lines = truth["lines"]
    extracted = [
        (
            _decimal(line.amount.value) if line.amount else None,
            _decimal(line.quantity.value) if line.quantity else None,
        )
        for line in document.lines
    ]
    used: set[int] = set()
    found = 0
    for item in truth_lines:
        amount = _decimal(item.get("amount"))
        quantity = _decimal(item.get("quantity"))
        for index, (got_amount, got_quantity) in enumerate(extracted):
            if index in used or got_amount != amount:
                continue
            if quantity is not None and got_quantity is not None and got_quantity != quantity:
                continue
            used.add(index)
            found += 1
            break
    score.lines_truth += len(truth_lines)
    score.lines_found += found
    score.lines_extracted += len(extracted)
    score.lines_spurious += len(extracted) - len(used)
    if found != len(truth_lines) or len(extracted) != len(used):
        wrong_critical = wrong_critical or bool(extracted and len(extracted) != len(used))
    score.docs += 1
    if document.needs_verification:
        score.docs_flagged += 1
    elif wrong_critical or found != len(truth_lines):
        score.false_safe += 1
    detail["lines"] = f"{found}/{len(truth_lines)} (+{len(extracted) - len(used)} extra)"
    detail["flagged"] = str(document.needs_verification)
    return detail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--pdfs", type=Path, required=True)
    parser.add_argument("--doc-type", default="invoice")
    parser.add_argument("--languages", default="eng")
    parser.add_argument("--only", default="", help="comma-separated file prefixes to score")
    arguments = parser.parse_args()

    truth = json.loads(arguments.truth.read_text(encoding="utf-8"))
    only = {item for item in arguments.only.split(",") if item}
    score = Score()
    for entry in truth["invoices"]:
        for prefix in entry["files"]:
            if only and prefix not in only:
                continue
            for path in sorted(arguments.pdfs.glob(f"{prefix}*.pdf")):
                hints = ParserHints(
                    doc_type=DocType(arguments.doc_type), file_format=FileFormat.PDF
                )
                started = time.monotonic()
                result = parse_pdf_isolated(
                    path.read_bytes(), hints, languages=tuple(arguments.languages.split("+"))
                )
                score.seconds += time.monotonic() - started
                if not result.documents:
                    score.docs += 1
                    score.docs_flagged += 1
                    print(
                        json.dumps({"file": prefix, "id": entry["id"], "error": result.reason_code})
                    )
                    continue
                document = result.documents[0]
                detail = _score_document(score, entry, document)
                print(json.dumps({"file": prefix, "id": entry["id"], **detail}))

    total_fields = sum(score.fields.values()) or 1
    summary = {
        "documents": score.docs,
        "header_field_accuracy": f"{score.fields['correct']}/{total_fields}",
        "header_wrong": score.fields["wrong"],
        "header_missing": score.fields["missing"],
        "per_field_correct": {
            name: f"{counts['correct']}/{sum(counts.values())}"
            for name, counts in sorted(score.per_field.items())
        },
        "line_recall": f"{score.lines_found}/{score.lines_truth}",
        "line_spurious": score.lines_spurious,
        "flagged_for_verification": f"{score.docs_flagged}/{score.docs}",
        "FALSE_SAFE": score.false_safe,
        "seconds": round(score.seconds, 1),
    }
    print(json.dumps({"summary": summary}, indent=2))
    return 1 if score.false_safe else 0


if __name__ == "__main__":
    raise SystemExit(main())
