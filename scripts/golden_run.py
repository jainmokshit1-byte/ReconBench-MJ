"""Phase 1 golden-set harness; the engine adapter remains a conservative stub."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    expected_status: str
    expected_link: str | None
    expected_reason_codes: tuple[str, ...]
    truth: dict[str, Any]


def load_cases(golden_dir: Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for truth_path in sorted(golden_dir.glob("*/truth.yaml")):
        raw = yaml.safe_load(truth_path.read_text(encoding="utf-8")) or {}
        cases.append(
            GoldenCase(
                case_id=truth_path.parent.name,
                expected_status=str(raw.get("expected_status", "Needs Review")),
                expected_link=raw.get("expected_link"),
                expected_reason_codes=tuple(raw.get("expected_reason_codes", [])),
                truth=raw,
            )
        )
    return cases


def run_stub(case: GoldenCase) -> dict[str, Any]:
    """Never auto-pass a case before the real engine exists."""

    del case
    return {"status": "Needs Review", "link": None, "reason_codes": []}


def summarize(
    cases: list[GoldenCase], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    false_matched = sum(
        prediction.get("status") == "Matched" and case.expected_status != "Matched"
        for case, prediction in zip(cases, predictions, strict=True)
    )
    correct_links = sum(
        prediction.get("link") == case.expected_link
        for case, prediction in zip(cases, predictions, strict=True)
        if case.expected_link is not None
    )
    predicted_link_count = sum(prediction.get("link") is not None for prediction in predictions)
    expected_link_count = sum(case.expected_link is not None for case in cases)
    straight_through = sum(
        prediction.get("status") == "Matched" for prediction in predictions
    )
    return {
        "case_count": len(cases),
        "false_matched": false_matched,
        "straight_through_rate": (straight_through / len(cases)) if cases else None,
        "link_precision": (
            correct_links / predicted_link_count if predicted_link_count else None
        ),
        "link_recall": correct_links / expected_link_count if expected_link_count else None,
        "extraction_accuracy": None,
        "engine": "phase-1-conservative-stub",
    }


def self_check() -> bool:
    negative_truth = GoldenCase(
        case_id="synthetic-false-matched-check",
        expected_status="Exception",
        expected_link="PO-SYNTH-1",
        expected_reason_codes=("QTY_OVER_PO",),
        truth={},
    )
    report = summarize([negative_truth], [{"status": "Matched", "link": "PO-SYNTH-1"}])
    return report["false_matched"] == 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden-dir", type=Path, default=ROOT / "golden")
    parser.add_argument(
        "--self-check", action="store_true", help="prove a false Matched is rejected"
    )
    args = parser.parse_args()

    if args.self_check and not self_check():
        print(
            "Golden harness self-check failed: false Matched was not detected.",
            file=sys.stderr,
        )
        return 1

    cases = load_cases(args.golden_dir)
    predictions = [run_stub(case) for case in cases]
    report = summarize(cases, predictions)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["false_matched"]:
        print("Golden gate failed: false Matched must remain zero.", file=sys.stderr)
        return 1
    if args.self_check:
        print("Self-check passed: the harness detects a synthetic false Matched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
