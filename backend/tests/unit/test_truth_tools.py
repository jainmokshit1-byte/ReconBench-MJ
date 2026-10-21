"""Truth tally and labeller agreement tools (accuracy plan WI-D5), synthetic data only."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from scripts.truth_agreement import compare, read_sheet
from scripts.truth_agreement import main as agreement_main
from scripts.truth_tally import tally


def _truth() -> dict[str, object]:
    return {
        "format": "reconbench-accuracy-truth/v2",
        "config": {"run_date": "2026-10-06"},
        "files": [
            {"id": "F1", "path": "invoice/a.pdf", "zone": "invoice"},
            {"id": "F2", "path": "po/b.xml", "zone": "po"},
            {"id": "F3", "path": "grn/c.pdf", "zone": "grn"},
            {"id": "F4", "path": "invoice/d.pdf", "zone": "invoice"},
        ],
        "sets": [
            {
                "id": "S01",
                "documents": [
                    {
                        "id": "S01-INV",
                        "doc_type": "invoice",
                        "sources": [{"file": "F1", "scan_type": "scan_clean", "handwritten": True}],
                        "header": {"doc_no": "SYN-INV-1", "grand_total": "100.00"},
                        "lines": [],
                        "links": ["S01-PO"],
                        "annotation_refs": ["SYN-PO-9"],
                        "tier": "S",
                        "split": "dev",
                    },
                    {
                        "id": "S01-PO",
                        "doc_type": "po",
                        "sources": [{"file": "F2"}],
                        "header": {"doc_no": "SYN-PO-1"},
                    },
                    {
                        "id": "S01-GRN",
                        "doc_type": "grn",
                        "sources": [{"file": "F3"}],
                        "header": {"doc_no": "SYN-GRN-1"},
                        "links": ["S01-PO"],
                    },
                ],
            },
            {
                "id": "S02",
                "documents": [
                    {
                        "id": "S02-INV",
                        "doc_type": "invoice",
                        "sources": [{"file": "F4"}, {"file": "F1"}],
                        "header": {"doc_no": "SYN-INV-2"},
                        "marketplace_order_no": ["000-0000000-0000000"],
                    }
                ],
            },
        ],
    }


def test_tally_counts_documents_sets_links_and_fields() -> None:
    result = tally(_truth())
    assert result["files"] == 4 and result["sets"] == 2
    assert result["complete_po_grn_invoice_sets"] == 1
    assert result["documents"] == 4
    assert result["documents_by_type"] == {"invoice": 2, "po": 1, "grn": 1}
    assert result["expected_links"] == 2 and result["documents_with_expected_links"] == 2
    assert result["documents_with_annotation_refs"] == 1
    assert result["documents_with_marketplace_order_no"] == 1
    assert result["header_field_presence"] == {"doc_no": 4, "grand_total": 1}
    assert result["files_by_zone"] == {"invoice": 2, "po": 1, "grn": 1}
    assert result["sources_per_document"] == {"1": 3, "2": 1}
    assert result["tiers"] == {"S": 1} and result["splits"] == {"dev": 1}
    assert result["source_scan_types"] == {"scan_clean": 1} and result["sources_handwritten"] == 1


def test_tally_prints_no_values_ids_or_file_names() -> None:
    text = json.dumps(tally(_truth()))
    for forbidden in ("SYN-INV", "SYN-PO", "SYN-GRN", "S01", "a.pdf", "000-0000000"):
        assert forbidden not in text


def test_tally_refuses_a_foreign_file() -> None:
    with pytest.raises(ValueError):
        tally({"format": "something-else"})


def _sheet(rows: list[tuple[str, str, str, str]]) -> dict[tuple[str, str], tuple[str, str]]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["invoice", "field", "status", "value"])
    writer.writerows(rows)
    return read_sheet(csv.DictReader(io.StringIO(buffer.getvalue())))


def test_agreement_normalises_money_references_and_gstin_case() -> None:
    a = _sheet(
        [
            ("I1", "grand_total", "present", "1,180.0"),
            ("I1", "po_refs", "present", "PO-2; PO-1"),
            ("I1", "vendor_gstin", "present", "27aapfu0939f1zv"),
            ("I1", "doc_no", "present", "INV-0O1"),
            ("I1", "doc_date", "illegible", ""),
        ]
    )
    b = _sheet(
        [
            ("I1", "grand_total", "present", "1180.00"),
            ("I1", "po_refs", "present", "po-1;po-2"),
            ("I1", "vendor_gstin", "present", "27AAPFU0939F1ZV"),
            ("I1", "doc_no", "present", "INV-001"),
            ("I1", "doc_date", "present", "2026-08-05"),
        ]
    )
    report = compare(a, b)
    assert report["per_field"]["grand_total"] == {"agree": 1}
    assert report["per_field"]["po_refs"] == {"agree": 1}
    assert report["per_field"]["vendor_gstin"] == {"agree": 1}
    # The letter O is not the digit 0: look-alikes are real disagreements.
    assert report["per_field"]["doc_no"] == {"disagree": 1}
    assert report["per_field"]["doc_date"] == {"illegible": 1}
    assert report["critical"]["agreement"] == "3/4"
    assert report["critical"]["illegible"] == 1


def test_agreement_counts_unmatched_rows_and_rejects_bad_sheets() -> None:
    a = _sheet([("I1", "doc_no", "present", "X"), ("I2", "doc_no", "present", "Y")])
    b = _sheet([("I1", "doc_no", "present", "X"), ("I3", "doc_no", "not_present", "")])
    report = compare(a, b)
    assert (report["only_in_a"], report["only_in_b"], report["invoices_in_both"]) == (1, 1, 1)
    with pytest.raises(ValueError):
        _sheet([("I1", "doc_no", "maybe", "X")])
    with pytest.raises(ValueError):
        _sheet([("I1", "doc_no", "present", "X"), ("I1", "doc_no", "present", "X")])


def test_command_line_gate_on_critical_agreement(tmp_path: Path) -> None:
    header = "invoice,field,status,value\n"
    (tmp_path / "a.csv").write_text(header + "I1,doc_no,present,A\nI1,grand_total,present,1\n")
    (tmp_path / "b.csv").write_text(header + "I1,doc_no,present,A\nI1,grand_total,present,2\n")
    args = ["truth_agreement", str(tmp_path / "a.csv"), str(tmp_path / "b.csv")]
    assert agreement_main(args) == 0
    assert agreement_main([*args, "--min-critical", "0.95"]) == 1
    assert agreement_main([*args, "--min-critical", "0.5"]) == 0
