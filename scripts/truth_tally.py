"""Counts-only tally of a hand-labelled truth file (accuracy plan WI-D5, step 1).

Prints how many files, documents, sets, links and header fields the truth file holds,
split by document type and (for format v3) by tier, split and scan type. It prints
counts only: never a field value, a file name or a document id. Use it to answer
"how many truth invoices do we have" without opening the file.

    python scripts/truth_tally.py [work/accuracy-truth/truth.json]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("work/accuracy-truth/truth.json")
_KNOWN_FORMATS_PREFIX = "reconbench-accuracy-truth/"
_COMPLETE_SET = frozenset({"po", "grn", "invoice"})


def tally(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return counts for a parsed truth file (v2, or v3 with tier/split/scan_type)."""

    declared = str(raw.get("format", ""))
    if not declared.startswith(_KNOWN_FORMATS_PREFIX):
        raise ValueError("not a reconbench accuracy truth file")
    files = list(raw.get("files", []))
    sets = list(raw.get("sets", []))
    documents = [(item, doc) for item in sets for doc in item.get("documents", [])]
    by_type = Counter(str(doc.get("doc_type")) for _, doc in documents)
    header_fields: Counter[str] = Counter()
    with_lines = 0
    with_links = 0
    expected_links = 0
    with_annotation_refs = 0
    with_marketplace = 0
    sources_per_doc: Counter[int] = Counter()
    tiers: Counter[str] = Counter()
    splits: Counter[str] = Counter()
    scan_types: Counter[str] = Counter()
    handwritten = 0
    for _, doc in documents:
        header = doc.get("header") or {}
        for key in header:
            header_fields[str(key)] += 1
        if doc.get("lines") is not None:
            with_lines += 1
        links = doc.get("links") or []
        if links:
            with_links += 1
            expected_links += len(links)
        if doc.get("annotation_refs"):
            with_annotation_refs += 1
        if doc.get("marketplace_order_no"):
            with_marketplace += 1
        sources_per_doc[len(doc.get("sources", []))] += 1
        if doc.get("tier"):
            tiers[str(doc["tier"])] += 1
        if doc.get("split"):
            splits[str(doc["split"])] += 1
        for source in doc.get("sources", []):
            if source.get("scan_type"):
                scan_types[str(source["scan_type"])] += 1
            if source.get("handwritten"):
                handwritten += 1
    complete = 0
    for item in sets:
        kinds = {str(doc.get("doc_type")) for doc in item.get("documents", [])}
        if kinds >= _COMPLETE_SET:
            complete += 1
    result: dict[str, Any] = {
        "format": declared,
        "files": len(files),
        "files_by_zone": dict(Counter(str(item.get("zone")) for item in files)),
        "files_by_extension": dict(
            Counter(Path(str(item.get("path", ""))).suffix.casefold() or "(none)" for item in files)
        ),
        "sets": len(sets),
        "complete_po_grn_invoice_sets": complete,
        "documents": len(documents),
        "documents_by_type": dict(by_type),
        "documents_with_lines": with_lines,
        "documents_with_expected_links": with_links,
        "expected_links": expected_links,
        "documents_with_annotation_refs": with_annotation_refs,
        "documents_with_marketplace_order_no": with_marketplace,
        "sources_per_document": {str(k): v for k, v in sorted(sources_per_doc.items())},
        "header_field_presence": dict(sorted(header_fields.items())),
    }
    if tiers or splits or scan_types or handwritten:
        result["tiers"] = dict(tiers)
        result["splits"] = dict(splits)
        result["source_scan_types"] = dict(scan_types)
        result["sources_handwritten"] = handwritten
    return result


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    if not path.exists():
        print(f"no truth file at {path}", file=sys.stderr)
        return 2
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps(tally(raw), indent=2, sort_keys=True))
    except (ValueError, KeyError, TypeError) as exc:
        print(f"cannot tally {path.name}: {type(exc).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
