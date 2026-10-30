"""Unit test for PD-5c accuracy scorer (offline, synthetic data only).

Verifies:
- correct / wrong / missing counts
- AI-alone selection (filtered by cross_check outcome in v2.cross_check)
- failed AI reading: rules arm scored; ai_as_used and ai_alone count fields/lines missing
- v2 selection with extraction_method='ai_first'
- v1 as derived_from_version_id (e.g. seq=6, not seq=1)
- unmatched document counting
- ambiguous truth files counting
- check that no value string, file name, or file path appears in the printed or exported output.
"""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from scripts.pd5c_score import main, wilson_interval


def test_wilson_interval() -> None:
    assert wilson_interval(0, 0) is None
    res = wilson_interval(10, 10)
    assert res is not None
    assert res[0] > 0.6
    assert res[1] == 1.0

    res_half = wilson_interval(5, 10)
    assert res_half is not None
    assert 0.0 < res_half[0] < 0.5 < res_half[1] < 1.0


def test_pd5c_score_synthetic(tmp_path: Path) -> None:
    # 1. Create synthetic samples and answer sheet (truth.json)
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()

    sample1_path = samples_dir / "inv1.pdf"
    sample1_bytes = b"synthetic pdf invoice content 1"
    sample1_path.write_bytes(sample1_bytes)
    sha1 = hashlib.sha256(sample1_bytes).hexdigest()

    sample2_path = samples_dir / "inv2.pdf"
    sample2_bytes = b"synthetic pdf invoice content 2"
    sample2_path.write_bytes(sample2_bytes)
    sha2 = hashlib.sha256(sample2_bytes).hexdigest()

    sample3_path = samples_dir / "inv3.pdf"
    sample3_bytes = b"synthetic pdf invoice content 3"
    sample3_path.write_bytes(sample3_bytes)

    truth_data = {
        "format": "reconbench-accuracy-truth/v2",
        "config": {"run_date": "2026-08-01"},
        "files": [
            {"id": "f1", "path": "inv1.pdf", "zone": "invoice"},
            {"id": "f2", "path": "inv2.pdf", "zone": "invoice"},
            {"id": "f3", "path": "inv3.pdf", "zone": "invoice"},
        ],
        "sets": [
            {
                "id": "set1",
                "documents": [
                    {
                        "id": "t_doc1",
                        "doc_type": "invoice",
                        "sources": [{"file": "f1"}],
                        "header": {
                            "doc_no": "INV-1001",
                            "doc_date": "2026-08-01",
                            "vendor_gstin": "27AAHCC0360R2ZA",
                            "vendor_name": "Acme Solutions Pvt Ltd",
                            "grand_total": 1180.00,
                            "taxable_total": 1000.00,
                        },
                        "lines": [{"amount": 1000.00, "description": "Consulting"}],
                    },
                    {
                        "id": "t_doc2",
                        "doc_type": "invoice",
                        "sources": [{"file": "f2"}],
                        "header": {
                            "doc_no": "INV-2002",
                            "doc_date": "2026-08-02",
                            "grand_total": 500.00,
                        },
                        "lines": [{"amount": 500.00, "description": "Hardware"}],
                    },
                    # Multi-doc on f3 to test ambiguous_truth_files counting
                    {
                        "id": "t_doc3a",
                        "doc_type": "invoice",
                        "sources": [{"file": "f3"}],
                        "header": {"doc_no": "INV-3001"},
                        "lines": [],
                    },
                    {
                        "id": "t_doc3b",
                        "doc_type": "invoice",
                        "sources": [{"file": "f3"}],
                        "header": {"doc_no": "INV-3002"},
                        "lines": [],
                    },
                ],
            }
        ],
    }
    truth_file = tmp_path / "truth.json"
    truth_file.write_text(json.dumps(truth_data), encoding="utf-8")

    # 2. Create synthetic readings dump
    # Doc 1: matched. v1 has seq=6 (derived_from_version_id), v2 has seq=7.
    #   - doc_no: correct in rules, correct in AI as used, cross_check agree -> AI alone
    #   - doc_date: wrong in rules, correct in AI as used, cross_check disagree -> AI alone
    #   - grand_total: missing in rules, correct in AI as used, cross_check ai_only -> AI alone
    #   - taxable_total: wrong in rules and AI as used, cross_check ocr_kept -> missing in AI alone
    #   - vendor_gstin: wrong in rules and AI as used, cross_check agree -> ai_ocr_agree_both_wrong!
    #   - line 1 amount: correct in rules and AI as used, cross_check agree -> AI alone
    # Doc 2: failed reading (state = 'failed')
    #   - rules arm (v1) doc_no correct, grand_total correct, line amount correct
    #   - ai_as_used and ai_alone: doc_no, doc_date, grand_total, line amount missing
    # Doc 4: unmatched SHA
    readings = [
        {
            "document_id": "00000000-0000-0000-0000-000000000001",
            "source_file_sha256": sha1,
            "page_count": 1,
            "reading_state": "ready",
            "reading_failure_code": None,
            "v1": {
                "version_id": "00000000-0000-0000-0000-000000000016",
                "seq": 6,
                "header": {
                    "doc_no": "INV-1001",  # correct
                    "doc_date": "2026-08-02",  # wrong
                    "grand_total": None,  # missing
                    "taxable_total": 999.00,  # wrong
                    "vendor_gstin": "27WRONGGSTIN1Z1",  # wrong
                    "vendor_name": "Acme Solutions Pvt Ltd",  # correct
                },
                "lines": [{"amount": 1000.00, "line_no": 1}],
            },
            "v2": {
                "version_id": "00000000-0000-0000-0000-000000000017",
                "seq": 7,
                "header": {
                    "doc_no": "INV-1001",  # correct
                    "doc_date": "2026-08-01",  # correct
                    "grand_total": 1180.00,  # correct
                    "taxable_total": 999.00,  # wrong
                    "vendor_gstin": "27WRONGGSTIN1Z1",  # wrong
                    "vendor_name": "Acme Solutions Pvt Ltd",  # correct
                },
                "lines": [{"amount": 1000.00, "line_no": 1}],
                "cross_check": [
                    {"field": "header:doc_no", "outcome": "agree"},
                    {"field": "header:doc_date", "outcome": "disagree"},
                    {"field": "header:grand_total", "outcome": "ai_only"},
                    {"field": "header:taxable_total", "outcome": "ocr_kept"},
                    {"field": "header:vendor_gstin", "outcome": "agree"},
                    {"field": "header:vendor_name", "outcome": "agree"},
                    {"field": "line:1:amount", "outcome": "agree"},
                ],
            },
        },
        {
            # Failed document: rules arm should still be scored; AI arms count all as missing
            "document_id": "00000000-0000-0000-0000-000000000002",
            "source_file_sha256": sha2,
            "page_count": 1,
            "reading_state": "failed",
            "reading_failure_code": "PROVIDER_UNAVAILABLE",
            "v1": {
                "version_id": "00000000-0000-0000-0000-000000000021",
                "seq": 1,
                "header": {
                    "doc_no": "INV-2002",  # correct
                    "doc_date": "2026-08-02",  # correct
                    "grand_total": 500.00,  # correct
                },
                "lines": [{"amount": 500.00, "line_no": 1}],
            },
            "v2": None,
        },
        {
            # Unmatched document
            "document_id": "00000000-0000-0000-0000-000000000004",
            "source_file_sha256": (
                "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
            ),
            "page_count": 1,
            "reading_state": "ready",
            "reading_failure_code": None,
            "v1": {"header": {}},
            "v2": {"header": {}},
        },
    ]
    readings_file = tmp_path / "readings.json"
    readings_file.write_text(json.dumps(readings), encoding="utf-8")
    out_file = tmp_path / "counts.json"

    # 3. Run scorer via main() and capture output
    buf = io.StringIO()
    with redirect_stdout(buf):
        ret = main(
            [
                "--readings",
                str(readings_file),
                "--truth",
                str(truth_file),
                "--samples",
                str(samples_dir),
                "--out",
                str(out_file),
            ]
        )
    assert ret == 0
    printed_output = buf.getvalue()

    # 4. Verify counts in output file
    assert out_file.is_file()
    out_data = json.loads(out_file.read_text(encoding="utf-8"))
    summary = out_data["summary"]
    assert summary["total_readings"] == 3
    assert summary["matched_documents"] == 2
    assert summary["failed_documents"] == 1
    assert summary["unmatched_documents"] == 1
    assert summary["deduplicated_documents"] == 0
    assert summary["ambiguous_truth_files"] == 1
    assert summary["ai_ocr_agree_both_wrong"] == 1  # vendor_gstin agreed but wrong

    arms = out_data["arms"]
    # Rules arm
    # doc_no: doc1 (correct) + doc2 (correct) = 2 correct
    r_doc_no = arms["rules"]["doc_no"]
    assert r_doc_no["correct"] == 2 and r_doc_no["wrong"] == 0
    # grand_total: doc1 missing, doc2 correct = 1 missing, 1 correct
    r_grand_total = arms["rules"]["grand_total"]
    assert r_grand_total["missing"] == 1 and r_grand_total["correct"] == 1
    # doc_date: doc1 wrong, doc2 correct = 1 wrong, 1 correct
    r_date = arms["rules"]["doc_date"]
    assert r_date["wrong"] == 1 and r_date["correct"] == 1
    # taxable_total: doc1 wrong
    r_taxable = arms["rules"]["taxable_total"]
    assert r_taxable["wrong"] == 1
    # line_amount: doc1 correct, doc2 correct = 2 correct
    r_lines = arms["rules"]["line_amount"]
    assert r_lines["correct"] == 2

    # AI as used arm
    # doc_no: doc1 correct, doc2 (failed) missing = 1 correct, 1 missing
    u_doc_no = arms["ai_as_used"]["doc_no"]
    assert u_doc_no["correct"] == 1 and u_doc_no["missing"] == 1
    # grand_total: doc1 correct, doc2 missing = 1 correct, 1 missing
    u_grand_total = arms["ai_as_used"]["grand_total"]
    assert u_grand_total["correct"] == 1 and u_grand_total["missing"] == 1
    # doc_date: doc1 correct, doc2 missing = 1 correct, 1 missing
    u_date = arms["ai_as_used"]["doc_date"]
    assert u_date["correct"] == 1 and u_date["missing"] == 1
    # line_amount: doc1 correct, doc2 missing = 1 correct, 1 missing
    u_lines = arms["ai_as_used"]["line_amount"]
    assert u_lines["correct"] == 1 and u_lines["missing"] == 1

    # AI alone arm (taxable_total was ocr_kept so treated as missing; doc2 failed so all missing)
    a_taxable = arms["ai_alone"]["taxable_total"]
    assert a_taxable["missing"] == 1
    a_grand = arms["ai_alone"]["grand_total"]
    assert a_grand["correct"] == 1 and a_grand["missing"] == 1
    a_date = arms["ai_alone"]["doc_date"]
    assert a_date["correct"] == 1 and a_date["missing"] == 1
    a_lines = arms["ai_alone"]["line_amount"]
    assert a_lines["correct"] == 1 and a_lines["missing"] == 1

    # 5. Check that NO value string or sensitive filename appears in printed or json output
    forbidden_strings = [
        "INV-1001",
        "INV-2002",
        "27AAHCC0360R2ZA",
        "27WRONGGSTIN1Z1",
        "Acme Solutions",
        "1180.00",
        "1000.00",
        "999.00",
        "Consulting",
        "Hardware",
        "inv1.pdf",
        "inv2.pdf",
        "inv3.pdf",
        str(samples_dir),
        str(truth_file),
    ]
    for forbidden in forbidden_strings:
        assert forbidden not in printed_output, f"Value '{forbidden}' leaked in printed report!"
        assert forbidden not in out_file.read_text(encoding="utf-8"), (
            f"Value '{forbidden}' leaked in output JSON!"
        )
