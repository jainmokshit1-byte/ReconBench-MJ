"""Run golden cases against the deterministic engine and report unsafe passes."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from reconbench_core.recon import (
    ReconciliationConfig,
    ReconciliationSnapshot,
    evaluate_components,
)
from reconbench_core.version import ENGINE_VERSION

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


def run_engine_case(case: GoldenCase) -> dict[str, Any]:
    """Evaluate a canonical snapshot stored in the golden truth record."""

    snapshot_raw = case.truth.get("snapshot")
    run_date_raw = case.truth.get("run_date")
    if not isinstance(snapshot_raw, dict) or not isinstance(run_date_raw, str):
        return {
            "status": "Needs Review",
            "link": None,
            "reason_codes": [],
            "adapter_error": "golden truth must include snapshot and run_date",
        }
    try:
        snapshot = ReconciliationSnapshot.model_validate(snapshot_raw)
        config_raw = case.truth.get("engine_config", {})
        if not isinstance(config_raw, dict):
            raise TypeError("engine_config must be an object")
        config = ReconciliationConfig.model_validate(config_raw)
        run_date = date.fromisoformat(run_date_raw)
        results = evaluate_components(snapshot, config, run_date)
    except (TypeError, ValidationError, ValueError):
        return {
            "status": "Needs Review",
            "link": None,
            "reason_codes": [],
            "adapter_error": "golden snapshot did not satisfy the engine input contract",
        }
    requested_invoice_id = case.truth.get("invoice_id")
    result = next(
        (
            item
            for item in results
            if requested_invoice_id is None
            or str(item.invoice_id) == str(requested_invoice_id)
        ),
        None,
    )
    if result is None:
        return {
            "status": "Needs Review",
            "link": None,
            "reason_codes": [],
            "adapter_error": "golden snapshot did not produce the requested invoice case",
        }
    labels = {
        "matched": "Matched",
        "matched_flags": "Matched with flags",
        "needs_review": "Needs Review",
        "exception": "Exception",
        "pending": "Pending",
        "duplicate": "Duplicate",
        "unlinked": "Unlinked",
    }
    link = result.po_nos[0] if len(result.po_nos) == 1 else None
    return {
        "status": labels[result.status.value],
        "link": link,
        "reason_codes": [code.value for code in result.reason_codes],
        "engine_version": result.engine_version,
        "rules_hash": result.rules_hash,
    }


def summarize(
    cases: list[GoldenCase], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    case_results = [
        {
            "case_id": case.case_id,
            "expected_status": case.expected_status,
            "predicted_status": prediction.get("status"),
            "expected_link": case.expected_link,
            "predicted_link": prediction.get("link"),
            "expected_reason_codes": list(case.expected_reason_codes),
            "predicted_reason_codes": prediction.get("reason_codes", []),
            "false_matched": (
                prediction.get("status") == "Matched"
                and case.expected_status != "Matched"
            ),
            "adapter_error": prediction.get("adapter_error"),
        }
        for case, prediction in zip(cases, predictions, strict=True)
    ]
    false_matched = sum(
        prediction.get("status") == "Matched" and case.expected_status != "Matched"
        for case, prediction in zip(cases, predictions, strict=True)
    )
    correct_links = sum(
        prediction.get("link") == case.expected_link
        for case, prediction in zip(cases, predictions, strict=True)
        if case.expected_link is not None
    )
    predicted_link_count = sum(
        prediction.get("link") is not None for prediction in predictions
    )
    expected_link_count = sum(case.expected_link is not None for case in cases)
    straight_through = sum(
        prediction.get("status") == "Matched" for prediction in predictions
    )
    unsupported_cases = sum("adapter_error" in prediction for prediction in predictions)
    return {
        "case_count": len(cases),
        "false_matched": false_matched,
        "unsupported_cases": unsupported_cases,
        "case_results": case_results,
        "gate_status": (
            "unverified_no_cases"
            if not cases
            else "unverified_invalid_truth"
            if unsupported_cases
            else "evaluated"
        ),
        "straight_through_rate": (straight_through / len(cases)) if cases else None,
        "link_precision": (
            correct_links / predicted_link_count if predicted_link_count else None
        ),
        "link_recall": correct_links / expected_link_count
        if expected_link_count
        else None,
        "extraction_accuracy": None,
        "engine": f"reconbench-core/{ENGINE_VERSION}",
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
    parser.add_argument(
        "--output", type=Path, help="also write the report to this JSON file"
    )
    args = parser.parse_args()

    if args.self_check and not self_check():
        print(
            "Golden harness self-check failed: false Matched was not detected.",
            file=sys.stderr,
        )
        return 1

    cases = load_cases(args.golden_dir)
    predictions = [run_engine_case(case) for case in cases]
    report = summarize(cases, predictions)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    if report["unsupported_cases"]:
        print("Golden gate could not evaluate one or more cases.", file=sys.stderr)
        return 2
    if report["false_matched"]:
        print("Golden gate failed: false Matched must remain zero.", file=sys.stderr)
        return 1
    if args.self_check:
        print("Self-check passed: the harness detects a synthetic false Matched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
