"""Unit tests for reconbench_tools trace formatting, marks, and masking."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from reconbench_tools.trace_format import (
    GREEN,
    RED,
    YELLOW,
    format_ai_reader_line,
    format_doc_header,
    format_fields_table,
    format_ingestion_line,
    format_ledger_section,
    format_lines_table,
    format_links_section,
    format_match_section,
    format_queue_line,
    format_run_mode,
    mark_fail,
    mark_ok,
    mark_pending,
    mark_warn,
    mask_amount,
    mask_gstin,
    mask_qty,
    mask_vendor,
)


def test_status_marks() -> None:
    """Test symbols and color application."""
    assert mark_ok(no_color=True) == "✓"
    assert mark_fail(no_color=True) == "✗"
    assert mark_pending(no_color=True) == "⏳"
    assert mark_warn(no_color=True) == "⚠"

    assert GREEN in mark_ok(no_color=False)
    assert RED in mark_fail(no_color=False)
    assert YELLOW in mark_pending(no_color=False)
    assert YELLOW in mark_warn(no_color=False)


def test_mask_gstin() -> None:
    """Test GSTIN masking and not-recorded fallback."""
    assert mask_gstin(None) == "not recorded"
    assert mask_gstin("") == "not recorded"

    gstin = "27AGGPP1121P1Z7"
    assert mask_gstin(gstin, mask=False) == "27AGGPP1121P1Z7"
    masked = mask_gstin(gstin, mask=True)
    assert masked == "27***********Z7"
    assert "AGGPP" not in masked


def test_mask_vendor() -> None:
    """Test vendor name masking and not-recorded fallback."""
    assert mask_vendor(None) == "not recorded"
    assert mask_vendor("") == "not recorded"

    vendor = "Aster Steelworks"
    assert mask_vendor(vendor, mask=False) == "Aster Steelworks"
    assert mask_vendor(vendor, mask=True) == "[MASKED VENDOR]"


def test_mask_amount() -> None:
    """Test monetary amount formatting and masking."""
    assert mask_amount(None) == "not recorded"
    assert mask_amount(Decimal("10030.00"), mask=False) == "10,030.00"
    assert mask_amount("8500", mask=False) == "8,500.00"
    assert mask_amount(10030.00, mask=True) == "₹***.**"


def test_mask_qty() -> None:
    """Test quantity formatting."""
    assert mask_qty(None) == "not recorded"
    assert mask_qty(Decimal("1.0000")) == "1"
    assert mask_qty(Decimal("2.5000")) == "2.5"


def test_format_doc_header() -> None:
    """Test DOCUMENT line formatting with and without mask."""
    doc = {
        "doc_no": "INV-2026-018",
        "doc_type": "invoice",
        "vendor_name": "Aster Steelworks",
        "vendor_rule": "gstin",
        "vendor_score": Decimal("1.00"),
    }
    unmasked = format_doc_header(doc, mask=False, no_color=True)
    assert "DOCUMENT" in unmasked
    assert "INV-2026-018" in unmasked
    assert "invoice" in unmasked
    assert "Aster Steelworks" in unmasked
    assert "(gstin rule, 1.00)" in unmasked

    masked = format_doc_header(doc, mask=True, no_color=True)
    assert "[MASKED VENDOR]" in masked
    assert "Aster Steelworks" not in masked


def test_format_ingestion_line() -> None:
    """Test INGESTION line formatting with IST timestamp."""
    ingest = {
        "status": "parsed",
        "uploaded_at": datetime(2026, 10, 22, 10, 40, 58),
        "parse_finished_at": datetime(2026, 10, 22, 10, 41, 30),
        "extraction_method": "pdf_ocr",
        "line_count": 2,
    }
    res = format_ingestion_line(ingest, no_color=True)
    assert "INGESTION ✓" in res
    assert "uploaded 22 Oct 16:10 IST" in res
    assert "parsed 22 Oct 16:11 IST" in res
    assert "PDF OCR" in res
    assert "2 lines" in res


def test_format_ai_reader_line() -> None:
    """Test AI READER line with suggestions and typed handwritten refs."""
    ai_info = {
        "provider": "vllm",
        "model": "qwen3.5:9b",
        "state": "succeeded",
        "latency_ms": 84000,
        "suggestion_count": 16,
        "handwritten_refs": [
            {"type": "PO", "text": "08-136"},
            {"type": "GRN", "text": "08-124"},
            {"type": "PO", "text": "SSD Sata"},
        ],
    }
    res = format_ai_reader_line(ai_info, no_color=True)
    assert "AI READER ✓" in res
    assert "vllm/qwen3.5:9b" in res
    assert "succeeded" in res
    assert "84 s" in res
    assert "16 suggestions" in res
    assert 'PO "08-136"' in res
    assert 'GRN "08-124"' in res
    assert 'PO "SSD Sata" (not a number)' in res

    empty_res = format_ai_reader_line(None, no_color=True)
    assert "not recorded" in empty_res


def test_format_fields_table() -> None:
    """Test FIELD comparison table, including date normalisation."""
    fields_data = [
        {
            "name": "invoice no",
            "stored_val": "24",
            "source": "OCR",
            "conf": Decimal("0.62"),
            "ai_val": "OSC/26-27/3877",
        },
        {
            "name": "total",
            "stored_val": Decimal("10030.00"),
            "source": "OCR",
            "conf": Decimal("0.97"),
            "ai_val": Decimal("10030.00"),
        },
        {
            "name": "date",
            "stored_val": "2026-08-28",
            "source": "rule",
            "conf": Decimal("0.98"),
            "ai_val": "28-Aug-26",
        },
    ]
    lines = format_fields_table(fields_data, mask=False, no_color=True)
    assert len(lines) == 4
    assert "FIELD" in lines[0]
    assert "STORED (source, conf)" in lines[0]
    assert "AI SUGGESTION" in lines[0]
    assert "invoice no" in lines[1]
    assert "24 (OCR, 0.62)" in lines[1]
    assert "OSC/26-27/3877" in lines[1]
    assert "⚠ differs" in lines[1]
    assert "total" in lines[2]
    assert "10,030.00 (OCR, 0.97)" in lines[2]
    assert "✓" in lines[2]
    # Date normalized comparison: 2026-08-28 vs 28-Aug-26 must be ✓
    assert "date" in lines[3]
    assert "2026-08-28" in lines[3]
    assert "28-Aug-26" in lines[3]
    assert "✓" in lines[3]
    assert "⚠ differs" not in lines[3]


def test_format_lines_table() -> None:
    """Test LINES table formatting."""
    doc_lines = [
        {
            "line_no": 1,
            "description": "500 GB SSD SATA",
            "quantity": Decimal("1.0000"),
            "rate": Decimal("8500.00"),
            "amount": Decimal("8500.00"),
        }
    ]
    out = format_lines_table(doc_lines, mask=False, no_color=True)
    assert len(out) == 2
    assert "LINES" in out[0]
    assert "500 GB SSD SATA" in out[1]
    assert "1" in out[1]
    assert "8,500.00" in out[1]


def test_format_links_section() -> None:
    """Test LINKS section with level (line N / header) and link deduplication."""
    links = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "link_type": "inv_po",
            "level": "line",
            "from_line_no": 1,
            "to_line_no": 1,
            "target_doc_type": "PO",
            "target_doc_no": "PO/26-27/08-227",
            "state": "confirmed",
            "confidence": Decimal("0.88"),
            "method": "content",
        }
    ]
    grn_link = {
        "id": "22222222-2222-2222-2222-222222222222",
        "link_type": "grn_po",
        "level": "header",
        "source_doc_no": "GRN/BCSL/25-26/08-210",
        "target_doc_no": "PO/26-27/08-227",
        "state": "auto",
        "confidence": Decimal("0.99"),
        "method": "po_ref",
    }
    # Pass grn_link twice to verify deduplication
    out = format_links_section(links, grn_links=[grn_link, grn_link], no_color=True)
    assert len(out) == 2
    assert "line 1 → PO PO/26-27/08-227 line 1 · confirmed · 0.88 · content" in out[0]
    assert "GRN GRN/BCSL/25-26/08-210 header → PO PO/26-27/08-227 · auto · 0.99" in out[1]


def test_format_ledger_section() -> None:
    """Test LEDGER section formatting."""
    ledger = [
        {
            "po_line_no": 1,
            "ordered": Decimal("1.0000"),
            "received_accepted": Decimal("1.0000"),
            "billed": Decimal("1.0000"),
            "open_qty": Decimal("0.0000"),
        }
    ]
    out = format_ledger_section(ledger, no_color=True)
    assert len(out) == 1
    assert "LEDGER (current, not a run snapshot)" in out[0]
    assert "PO line 1 · ordered 1 · received 1 · billed 1 · open 0" in out[0]


def test_format_match_section() -> None:
    """Test MATCH section formatting."""
    case_info = {
        "run_id": "01a16248-4558-735c-8233-cdbc7a070b1b",
        "status": "matched",
        "reason_codes": [],
    }
    line_results = [
        {
            "line_no": 1,
            "po_qty": Decimal("1.0000"),
            "po_rate": Decimal("8500.00"),
            "grn_accepted_cum": Decimal("1.0000"),
            "billed_qty": Decimal("1.0000"),
            "payable_rate": Decimal("8500.00"),
            "status": "matched",
            "reason_codes": [],
        }
    ]
    out = format_match_section(case_info, line_results, mask=False, no_color=True)
    assert len(out) == 2
    assert "MATCH" in out[0]
    assert "run R-01a16248 · Matched · reasons: none" in out[0]
    assert "line 1  PO 1 × 8,500.00 | GRN accepted 1 | INV 1 × 8,500.00   ✓" in out[1]


def test_format_run_mode() -> None:
    """Test run summary table formatting."""
    run_info = {
        "id": "01a16248-4558-735c-8233-cdbc7a070b1b",
        "status": "succeeded",
        "started_at": datetime(2026, 10, 22, 10, 43, 2),
        "finished_at": datetime(2026, 10, 22, 10, 43, 6),
        "status_counts": {"matched": 2, "needs_review": 2, "unlinked": 3},
    }
    cases = [
        {
            "doc_no": "OSC/26-27/3877",
            "status": "matched",
            "reason_codes": [],
        },
        {
            "doc_no": "267",
            "status": "needs_review",
            "reason_codes": ["TOTAL_MISMATCH", "LOW_CONFIDENCE_DATA"],
        },
    ]
    out = format_run_mode(run_info, cases, no_color=True)
    assert len(out) == 4
    expected_header = (
        "RUN R-01a16248 · succeeded · 22 Oct 16:13 IST · 4 s · "
        "Matched 2 · Needs review 2 · Unlinked 3"
    )
    assert expected_header in out[0]
    assert "INVOICE" in out[1] and "STATUS" in out[1] and "REASONS" in out[1]
    assert "OSC/26-27/3877   Matched" in out[2]
    assert "—" in out[2]
    assert "267              Needs Review" in out[3]
    assert "TOTAL_MISMATCH, LOW_CONFIDENCE_DATA" in out[3]


def test_format_queue_line() -> None:
    """Test QUEUES line formatting."""
    counts = {
        "ingest": (0, 0),
        "parse": (0, 1),
        "mapping": (313, 1),
        "reconcile": (0, 0),
        "exports": (0, 0),
        "ai": (1, 0),
    }
    line = format_queue_line(counts, no_color=True)
    expected_q = (
        "QUEUES  ingest 0/0 · parse 0/1 · mapping 313/1 · "
        "reconcile 0/0 · exports 0/0 · ai 1/0"
    )
    assert line == expected_q


def test_static_queries_no_forbidden_keywords() -> None:
    """Static security audit: trace_queries.py contains no write or DDL operations."""
    queries_file = Path(__file__).resolve().parents[2] / "reconbench_tools" / "trace_queries.py"
    assert queries_file.exists(), f"File {queries_file} does not exist"

    content = queries_file.read_text(encoding="utf-8")

    forbidden_keywords = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "ALTER",
        "DROP",
        "TRUNCATE",
        "COPY",
        "GRANT",
        "REVOKE",
        "FOR UPDATE",
        "FOR SHARE",
    ]

    # Search for SQL keywords inside quotes or queries
    lines = content.splitlines()
    for idx, line in enumerate(lines, start=1):
        clean_line = line.strip()
        # Skip comments
        if (
            clean_line.startswith("#")
            or clean_line.startswith('"""')
            or clean_line.startswith("'''")
        ):
            continue
        for kw in forbidden_keywords:
            # Match whole word keyword in sql
            pattern = rf"\b{re.escape(kw)}\b"
            if re.search(pattern, clean_line, re.IGNORECASE):
                # Ensure it's not a python identifier like error_code, updated_at, etc.
                if kw in ("UPDATE", "DELETE", "ALTER", "DROP", "INSERT"):
                    # Check if it is a SQL statement keyword, not column name like updated_at
                    if re.search(rf"\b{kw}\s+[A-Za-z_]", clean_line, re.IGNORECASE):
                        pytest.fail(
                            f"Forbidden keyword '{kw}' on line {idx}: {line[:40]}"
                        )
                elif kw in (
                    "FOR UPDATE", "FOR SHARE", "TRUNCATE", "DROP", "ALTER", "GRANT", "REVOKE"
                ):
                    pytest.fail(
                        f"Forbidden keyword '{kw}' on line {idx}: {line[:40]}"
                    )


def test_handle_doc_without_version_or_source_file(capsys: pytest.CaptureFixture[str]) -> None:
    """Test trace handle_doc when document has current_version_id=None.

    Verifies fix for AttributeError on line 196 (ver.get(...) when ver is None).
    """
    from unittest.mock import MagicMock, patch
    from uuid import uuid4

    from reconbench_tools.trace import handle_doc

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    ws_id = uuid4()
    doc_id = uuid4()
    doc_data = {
        "id": doc_id,
        "doc_no": "LBAAAC0270369550",
        "doc_type": "invoice",
        "current_version_id": None,
        "first_seen_at": datetime(2026, 10, 22, 10, 0, 0),
        "vendor_name": "Test Vendor",
        "vendor_rule": "name",
        "vendor_score": Decimal("0.95"),
    }

    with (
        patch("reconbench_tools.trace.find_document", return_value=doc_data),
        patch("reconbench_tools.trace.get_document_version", return_value=None),
        patch("reconbench_tools.trace.get_source_file", return_value=None),
        patch("reconbench_tools.trace.get_doc_lines", return_value=[]),
        patch("reconbench_tools.trace.get_document_links", return_value=([], [])),
        patch("reconbench_tools.trace.get_po_ledger", return_value=[]),
        patch("reconbench_tools.trace.get_recon_case_and_lines", return_value=(None, [])),
    ):
        code = handle_doc(
            mock_engine,
            ws_id,
            "LBAAAC0270369550",
            mask=True,
            no_color=True,
        )

    assert code == 0
    captured = capsys.readouterr().out
    assert "DOCUMENT" in captured
    assert "LBAAAC0270369550" in captured
    assert "INGESTION" in captured

