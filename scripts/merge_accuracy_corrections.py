"""Merge a downloaded AC-7 correction patch into a separate Truth v2 file.

The patch contains application document IDs, while Truth v2 has owner-assigned
document IDs. Require an explicit mapping so a correction can never be applied
to a document guessed from a filename or document number.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

TRUTH_FORMAT = "reconbench-accuracy-truth/v2"
PATCH_FORMAT = "reconbench-accuracy-corrections/v1"
HEADER_FIELDS = frozenset(
    {
        "doc_no",
        "doc_date",
        "vendor_gstin",
        "vendor_name",
        "buyer_gstin",
        "taxable_total",
        "cgst",
        "sgst",
        "igst",
        "grand_total",
        "po_refs",
    }
)
LINE_FIELDS = frozenset({"description", "quantity", "rate", "amount"})


class CorrectionMergeError(ValueError):
    """A correction patch cannot be safely applied to the selected truth file."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CorrectionMergeError(f"{label} must be a JSON object")
    return value


def merge_corrections(
    truth: dict[str, Any],
    patch: dict[str, Any],
    mapping: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], int, int]:
    """Return an updated Truth v2 object and aggregate document/field counts."""

    if truth.get("format") != TRUTH_FORMAT:
        raise CorrectionMergeError(f"truth must declare format {TRUTH_FORMAT!r}")
    if patch.get("format") != PATCH_FORMAT:
        raise CorrectionMergeError(f"patch must declare format {PATCH_FORMAT!r}")
    raw_documents = patch.get("documents")
    if not isinstance(raw_documents, list):
        raise CorrectionMergeError("patch documents must be a list")
    if any(
        not isinstance(key, str) or not isinstance(value, dict) for key, value in mapping.items()
    ):
        raise CorrectionMergeError("mapping must map application document IDs to mapping objects")

    merged_truth = deepcopy(truth)
    targets: dict[str, dict[str, Any]] = {}
    sets = merged_truth.get("sets")
    if not isinstance(sets, list):
        raise CorrectionMergeError("truth sets must be a list")
    for truth_set in sets:
        set_object = _object(truth_set, "truth set")
        documents = set_object.get("documents")
        if not isinstance(documents, list):
            raise CorrectionMergeError("truth set documents must be a list")
        for item in documents:
            document = _object(item, "truth document")
            doc_id = document.get("id")
            if isinstance(doc_id, str):
                if doc_id in targets:
                    raise CorrectionMergeError(f"truth document id {doc_id!r} is not unique")
                targets[doc_id] = document

    application_ids: set[str] = set()
    target_ids: set[str] = set()
    updated_documents = 0
    updated_fields = 0
    for raw in raw_documents:
        item = _object(raw, "correction document")
        application_id = item.get("application_document_id")
        doc_type = item.get("doc_type")
        if not isinstance(application_id, str) or not application_id:
            raise CorrectionMergeError("each correction needs an application_document_id")
        if application_id in application_ids:
            raise CorrectionMergeError(
                f"application document {application_id!r} appears more than once"
            )
        application_ids.add(application_id)
        mapping_entry = mapping.get(application_id)
        if mapping_entry is None:
            raise CorrectionMergeError(
                f"no truth document mapping for application document {application_id!r}"
            )
        truth_id = mapping_entry.get("truth_document_id")
        if not isinstance(truth_id, str) or not truth_id:
            raise CorrectionMergeError(
                f"mapping for application document {application_id!r} needs a truth_document_id"
            )
        line_mappings = mapping_entry.get("lines", {})
        if not isinstance(line_mappings, dict) or any(
            not isinstance(key, str)
            or not key.isdigit()
            or int(key) < 1
            or str(int(key)) != key
            or not (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 1
                or value == "append"
            )
            for key, value in line_mappings.items()
        ):
            raise CorrectionMergeError(
                f"line mappings for application document {application_id!r} "
                "must map app line numbers to truth positions or 'append'"
            )
        if truth_id in target_ids:
            raise CorrectionMergeError(
                f"multiple application documents map to truth document {truth_id!r}"
            )
        target_ids.add(truth_id)
        target = targets.get(truth_id)
        if target is None:
            raise CorrectionMergeError(f"truth document {truth_id!r} was not found")
        if target.get("doc_type") != doc_type:
            raise CorrectionMergeError(
                f"document type mismatch for application document {application_id!r}"
            )

        header_patch = _object(item.get("header", {}), "correction header")
        if set(header_patch) - HEADER_FIELDS:
            raise CorrectionMergeError(
                f"unsupported truth header fields: {sorted(set(header_patch) - HEADER_FIELDS)}"
            )
        if header_patch:
            header = _object(target.setdefault("header", {}), "truth header")
            header.update(header_patch)
            updated_fields += len(header_patch)

        raw_lines = item.get("lines", [])
        if not isinstance(raw_lines, list):
            raise CorrectionMergeError("correction lines must be a list")
        truth_lines = target.get("lines")
        if raw_lines and not isinstance(truth_lines, list):
            raise CorrectionMergeError(f"truth document {truth_id!r} has no editable line list")
        seen_line_numbers: set[int] = set()
        seen_truth_line_numbers: set[int] = set()
        for raw_line in raw_lines:
            correction_line = _object(raw_line, "correction line")
            line_no = correction_line.get("line_no")
            fields = _object(correction_line.get("fields", {}), "correction line fields")
            if not isinstance(line_no, int) or isinstance(line_no, bool) or line_no < 1:
                raise CorrectionMergeError("correction line numbers must be positive integers")
            if not fields:
                raise CorrectionMergeError("each correction line needs at least one field")
            if line_no in seen_line_numbers:
                raise CorrectionMergeError(f"line {line_no} appears more than once")
            seen_line_numbers.add(line_no)
            unsupported = set(fields) - LINE_FIELDS
            if unsupported:
                raise CorrectionMergeError(f"unsupported truth line fields: {sorted(unsupported)}")
            assert isinstance(truth_lines, list)
            mapped_line_no = line_mappings.get(str(line_no))
            if mapped_line_no is None:
                raise CorrectionMergeError(
                    f"application line {line_no} has no explicit truth line mapping "
                    f"for {truth_id!r}"
                )
            if mapped_line_no == "append":
                seen_truth_line_numbers.add(len(truth_lines) + 1)
                truth_line = {}
                truth_lines.append(truth_line)
            elif mapped_line_no in seen_truth_line_numbers:
                raise CorrectionMergeError(
                    f"multiple application lines map to truth line {mapped_line_no} "
                    f"for {truth_id!r}"
                )
            elif mapped_line_no > len(truth_lines):
                raise CorrectionMergeError(
                    f"truth line {mapped_line_no} is outside the {len(truth_lines)} truth lines "
                    f"for {truth_id!r}"
                )
            else:
                seen_truth_line_numbers.add(mapped_line_no)
                truth_line = _object(truth_lines[mapped_line_no - 1], "truth line")
            truth_line.update(fields)
            updated_fields += len(fields)

        updated_documents += 1
    return merged_truth, updated_documents, updated_fields


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorrectionMergeError(f"could not read JSON input {path}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", required=True, type=Path, help="existing Truth v2 file")
    parser.add_argument(
        "--corrections", required=True, type=Path, help="downloaded AC-7 JSON patch"
    )
    parser.add_argument(
        "--mapping",
        required=True,
        type=Path,
        help="JSON object: app document ID to explicit truth document and line mapping",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="new Truth v2 output path; must differ from --truth",
    )
    args = parser.parse_args()
    if args.truth.resolve() == args.output.resolve():
        parser.error("--output must be a separate file from --truth")
    try:
        truth = _read_json(args.truth)
        patch = _read_json(args.corrections)
        raw_mapping = _read_json(args.mapping)
        if any(not isinstance(value, dict) for value in raw_mapping.values()):
            raise CorrectionMergeError("mapping values must be objects")
        if args.output.exists():
            parser.error("--output already exists; choose a new separate output file")
        updated, documents, fields = merge_corrections(truth, patch, raw_mapping)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except CorrectionMergeError as exc:
        parser.error(str(exc))
    print(f"Updated truth documents: {documents}; corrected fields: {fields}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
