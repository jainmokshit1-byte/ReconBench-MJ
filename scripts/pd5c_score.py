"""PD-5c accuracy scorer: rules vs AI as used vs AI alone.

Compares test document readings against ground truth.
Counts only: never prints or writes values, file names or paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from reconbench_core.schema import DocType

from scripts import accuracy_vision_eval
from scripts.accuracy_bench import (
    _HEADER_FIELDS,
    TruthDocument,
    _decimal,
    _normal,
    _same,
    load_truth,
)

_Z95 = 1.959963984540054

HEADER_KEY_MAP = {
    "cgst": "cgst_total",
    "sgst": "sgst_total",
    "igst": "igst_total",
}


def wilson_interval(
    successes: int, total: int, z: float = _Z95
) -> tuple[float, float] | None:
    """Wilson 95% confidence interval [lower, upper] (None when total is 0)."""
    if total <= 0:
        return None
    lower = accuracy_vision_eval.wilson_lower(successes, total, z=z)
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    upper = round((centre + margin) / denominator, 4)
    return (lower if lower is not None else 0.0, min(1.0, max(0.0, upper)))


@dataclass
class FieldCounts:
    correct: int = 0
    wrong: int = 0
    missing: int = 0

    @property
    def total(self) -> int:
        return self.correct + self.wrong + self.missing

    @property
    def accuracy(self) -> float | None:
        return round(self.correct / self.total, 4) if self.total > 0 else None

    @property
    def wilson95(self) -> tuple[float, float] | None:
        return wilson_interval(self.correct, self.total)

    def as_dict(self) -> dict[str, Any]:
        return {
            "correct": self.correct,
            "wrong": self.wrong,
            "missing": self.missing,
            "n": self.total,
            "accuracy": self.accuracy,
            "wilson95": self.wilson95,
        }


@dataclass
class ScoreReport:
    total_readings: int = 0
    matched_documents: int = 0
    unmatched_documents: int = 0
    failed_documents: int = 0
    deduplicated_documents: int = 0
    ambiguous_truth_files: int = 0
    ai_ocr_agree_both_wrong: int = 0
    arms: dict[str, dict[str, FieldCounts]] = field(
        default_factory=lambda: {
            "rules": defaultdict(FieldCounts),
            "ai_as_used": defaultdict(FieldCounts),
            "ai_alone": defaultdict(FieldCounts),
        }
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "total_readings": self.total_readings,
                "matched_documents": self.matched_documents,
                "unmatched_documents": self.unmatched_documents,
                "failed_documents": self.failed_documents,
                "deduplicated_documents": self.deduplicated_documents,
                "ambiguous_truth_files": self.ambiguous_truth_files,
                "ai_ocr_agree_both_wrong": self.ai_ocr_agree_both_wrong,
            },
            "arms": {
                arm: {name: counts.as_dict() for name, counts in fields.items()}
                for arm, fields in self.arms.items()
            },
        }


def _cross_check_map(cross_checks: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(cross_checks, list):
        return {}
    res: dict[str, dict[str, Any]] = {}
    for item in cross_checks:
        if isinstance(item, dict) and "field" in item:
            res[str(item["field"])] = item
    return res


def _get_header_val(header: dict[str, Any], name: str) -> Any:
    db_col = HEADER_KEY_MAP.get(name, name)
    val = header.get(name)
    if val is None and db_col != name:
        val = header.get(db_col)
    return val


def _score_header_field(
    expected_raw: Any,
    actual_raw: Any,
    field_name: str,
) -> str:
    expected = _normal(field_name, expected_raw)
    actual = _normal(field_name, actual_raw)
    if expected is None and actual is None:
        return "not_in_truth"
    if expected is None:
        return "unexpected"
    if actual is None:
        return "missing"
    if _same(field_name, expected, actual):
        return "correct"
    return "wrong"


def _score_line_amounts(
    truth_lines: list[dict[str, Any]],
    extracted_lines: list[dict[str, Any]],
) -> list[str]:
    expected_amounts = [
        _decimal(item.get("amount"))
        for item in truth_lines
        if item.get("amount") is not None
    ]
    got_amounts = [
        _decimal(item.get("amount"))
        for item in extracted_lines
        if item.get("amount") is not None and item.get("amount") is not False
    ]
    used: set[int] = set()
    outcomes: list[str] = []
    for exp in expected_amounts:
        match_idx = None
        for i, got in enumerate(got_amounts):
            if i in used:
                continue
            if exp == got:
                match_idx = i
                break
        if match_idx is not None:
            used.add(match_idx)
            outcomes.append("correct")
        else:
            unmatched = [i for i in range(len(got_amounts)) if i not in used]
            if unmatched:
                used.add(unmatched[0])
                outcomes.append("wrong")
            else:
                outcomes.append("missing")
    return outcomes


def score_readings(
    readings: list[dict[str, Any]],
    truth_by_hash: dict[str, TruthDocument],
) -> ScoreReport:
    report = ScoreReport()
    report.total_readings = len(readings)
    seen_hashes: set[str] = set()

    for reading in readings:
        sha256 = reading.get("source_file_sha256")
        if not sha256 or sha256 not in truth_by_hash:
            report.unmatched_documents += 1
            continue

        if sha256 in seen_hashes:
            report.deduplicated_documents += 1
            continue
        seen_hashes.add(sha256)

        truth_doc = truth_by_hash[sha256]
        report.matched_documents += 1

        state = reading.get("reading_state")
        v1 = reading.get("v1") or {}
        v2 = reading.get("v2") or {}
        v1_header = v1.get("header") or {}
        v1_lines = v1.get("lines") or []
        truth_lines = [dict(ln) for ln in (truth_doc.lines or ())]

        # Score Rules arm (v1)
        for name in _HEADER_FIELDS:
            if name not in truth_doc.header:
                continue
            exp_val = truth_doc.header.get(name)
            v1_val = _get_header_val(v1_header, name)
            res1 = _score_header_field(exp_val, v1_val, name)
            if res1 in ("correct", "wrong", "missing"):
                counts = report.arms["rules"][name]
                setattr(counts, res1, getattr(counts, res1) + 1)

        if truth_lines:
            for outcome in _score_line_amounts(truth_lines, v1_lines):
                counts = report.arms["rules"]["line_amount"]
                setattr(counts, outcome, getattr(counts, outcome) + 1)

        # If AI reading failed (or v2 missing):
        # In ai_as_used and ai_alone count every truth field and truth line as missing.
        if state == "failed" or not v2:
            report.failed_documents += 1
            for name in _HEADER_FIELDS:
                if name not in truth_doc.header:
                    continue
                exp_val = truth_doc.header.get(name)
                if _normal(name, exp_val) is not None:
                    report.arms["ai_as_used"][name].missing += 1
                    report.arms["ai_alone"][name].missing += 1
            if truth_lines:
                for item in truth_lines:
                    if item.get("amount") is not None:
                        report.arms["ai_as_used"]["line_amount"].missing += 1
                        report.arms["ai_alone"]["line_amount"].missing += 1
            continue

        # Normal scoring for successful AI version (v2)
        v2_header = v2.get("header") or {}
        v2_lines = v2.get("lines") or []
        cross_checks = _cross_check_map(v2.get("cross_check"))

        # Header fields scoring
        for name in _HEADER_FIELDS:
            if name not in truth_doc.header:
                continue
            exp_val = truth_doc.header.get(name)

            # AI as used arm (v2)
            v2_val = _get_header_val(v2_header, name)
            res2 = _score_header_field(exp_val, v2_val, name)
            if res2 in ("correct", "wrong", "missing"):
                counts = report.arms["ai_as_used"][name]
                setattr(counts, res2, getattr(counts, res2) + 1)

            # AI alone arm
            # Field is active in AI alone if cross_check outcome in {agree, disagree, ai_only}
            cc_entry = cross_checks.get(f"header:{name}") or cross_checks.get(name)
            cc_outcome = cc_entry.get("outcome") if cc_entry else None
            ai_has_field = cc_outcome in {"agree", "disagree", "ai_only"}
            v2_ai_alone = v2_val if ai_has_field else None
            res3 = _score_header_field(exp_val, v2_ai_alone, name)
            if res3 in ("correct", "wrong", "missing"):
                counts = report.arms["ai_alone"][name]
                setattr(counts, res3, getattr(counts, res3) + 1)

            # Check if AI and OCR agree but both are wrong
            if cc_outcome == "agree" and res2 == "wrong":
                report.ai_ocr_agree_both_wrong += 1

        # Line amount scoring
        if truth_lines:
            # AI as used lines
            for outcome in _score_line_amounts(truth_lines, v2_lines):
                counts = report.arms["ai_as_used"]["line_amount"]
                setattr(counts, outcome, getattr(counts, outcome) + 1)

            # AI alone lines
            ai_alone_lines: list[dict[str, Any]] = []
            for ln in v2_lines:
                lno = ln.get("line_no")
                cc_ln = cross_checks.get(f"line:{lno}:amount") or cross_checks.get(
                    f"line:{lno}"
                )
                cc_ln_outcome = cc_ln.get("outcome") if cc_ln else None
                if cc_ln_outcome in {"agree", "disagree", "ai_only"}:
                    ai_alone_lines.append(ln)
                else:
                    ai_alone_lines.append({"amount": None})

            for outcome in _score_line_amounts(truth_lines, ai_alone_lines):
                counts = report.arms["ai_alone"]["line_amount"]
                setattr(counts, outcome, getattr(counts, outcome) + 1)

    return report


def print_counts_report(report: ScoreReport) -> None:
    """Print counts only, never values, file names or paths."""
    print("=" * 60)
    print("PD-5C ACCURACY SCORES (COUNTS ONLY)")
    print("=" * 60)
    s = report.as_dict()["summary"]
    print(f"Total readings:          {s['total_readings']}")
    print(f"Matched documents:       {s['matched_documents']}")
    print(f"Unmatched documents:     {s['unmatched_documents']}")
    print(f"Failed documents:        {s['failed_documents']}")
    print(f"Deduplicated documents:  {s['deduplicated_documents']}")
    print(f"Ambiguous truth files:   {s['ambiguous_truth_files']}")
    print(f"AI/OCR agree both wrong: {s['ai_ocr_agree_both_wrong']}")
    print("-" * 60)

    for arm in ("rules", "ai_as_used", "ai_alone"):
        print(f"\nARM: {arm.upper()}")
        print(
            f"{'Field':<18} {'Correct':>8} {'Wrong':>8} {'Missing':>8} "
            f"{'Total':>8} {'Accuracy':>10} {'Wilson 95% Interval':>22}"
        )
        print("-" * 86)
        fields = report.arms[arm]
        for name in sorted(fields.keys()):
            cnt = fields[name]
            acc_str = f"{cnt.accuracy * 100:.1f}%" if cnt.accuracy is not None else "-"
            w_str = (
                f"[{cnt.wilson95[0]:.3f}, {cnt.wilson95[1]:.3f}]"
                if cnt.wilson95
                else "-"
            )
            print(
                f"{name:<18} {cnt.correct:>8} {cnt.wrong:>8} {cnt.missing:>8} "
                f"{cnt.total:>8} {acc_str:>10} {w_str:>22}"
            )
    print("=" * 86)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score PD-5c readings against truth (counts only)"
    )
    parser.add_argument(
        "--readings", required=True, type=Path, help="Path to dump readings JSON"
    )
    parser.add_argument("--truth", required=True, type=Path, help="Path to truth JSON")
    parser.add_argument(
        "--samples", required=True, type=Path, help="Path to samples folder"
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="Output counts JSON path"
    )
    args = parser.parse_args(argv)

    if not args.readings.is_file():
        print(f"Error: readings file not found: {args.readings}", file=sys.stderr)
        return 1
    if not args.truth.is_file():
        print(f"Error: truth file not found: {args.truth}", file=sys.stderr)
        return 1
    if not args.samples.is_dir():
        print(f"Error: samples directory not found: {args.samples}", file=sys.stderr)
        return 1

    truth = load_truth(args.truth)
    truth_by_hash: dict[str, TruthDocument] = {}
    ambiguous_count = 0
    for t_file in truth.files:
        sample_file = args.samples / t_file.path
        if sample_file.is_file():
            content = sample_file.read_bytes()
            h = hashlib.sha256(content).hexdigest()
            docs = [
                d
                for d in truth.for_file(t_file.file_id)
                if (
                    d.doc_type == DocType.INVOICE
                    or getattr(d.doc_type, "value", d.doc_type) == "invoice"
                )
            ]
            if len(docs) > 1:
                ambiguous_count += 1
            if docs:
                truth_by_hash[h] = docs[0]

    with open(args.readings, encoding="utf-8") as f:
        readings_data = json.load(f)

    if not isinstance(readings_data, list):
        print("Error: readings JSON must be a list of documents", file=sys.stderr)
        return 1

    report = score_readings(readings_data, truth_by_hash)
    report.ambiguous_truth_files = ambiguous_count
    print_counts_report(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report.as_dict(), f, indent=2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
