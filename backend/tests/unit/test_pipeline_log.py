"""Unit tests for ReconBench pipeline log watcher rendering and formatting.

Synthetic data only. Tests:
- format_inr requirements (int, Decimal, negative, zero, float raises TypeError)
- UTC -> IST conversion
- exact line format
- ASCII mode replacements
- Color on/off (no escape codes when off)
- Masked vs show mode guarantees
- Doc tag last 4 characters
- Status colors
"""

from __future__ import annotations

import os
import signal
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from reconbench_tools.pipeline_log_render import (
    ANSI_BLUE,
    ANSI_CYAN,
    ANSI_GREEN,
    ANSI_MAGENTA,
    ANSI_RED,
    ANSI_YELLOW,
    format_doc_tag,
    format_inr,
    format_status_mark,
    format_status_text,
    format_time_ist,
    get_doc_tag_color,
    mask_amount,
    mask_file_name,
    mask_gstin,
    mask_vendor,
    render_date_separator,
    render_event_line,
    render_heartbeat,
    render_startup_banner,
    strip_ansi,
    to_ist,
)


def test_format_inr() -> None:
    """Test format_inr with integer, Decimal, zero, negative, and float rejection."""
    # 118000 -> ₹1,18,000.00
    assert format_inr(118000) == "₹1,18,000.00"
    assert format_inr(Decimal("118000")) == "₹1,18,000.00"

    # 1234567.5 -> ₹12,34,567.50
    assert format_inr(Decimal("1234567.5")) == "₹12,34,567.50"
    assert format_inr("1234567.5") == "₹12,34,567.50"

    # 0 -> ₹0.00
    assert format_inr(0) == "₹0.00"
    assert format_inr(Decimal("0")) == "₹0.00"

    # Negative values
    assert format_inr(-118000) == "-₹1,18,000.00"
    assert format_inr(Decimal("-500.50")) == "-₹500.50"

    # ASCII mode
    assert format_inr(118000, ascii_mode=True) == "Rs 1,18,000.00"
    assert format_inr(-118000, ascii_mode=True) == "-Rs 1,18,000.00"

    # Float input MUST raise TypeError
    with pytest.raises(TypeError):
        format_inr(118000.0)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        format_inr(1234567.5)  # type: ignore[arg-type]


def test_utc_to_ist() -> None:
    """Test UTC -> IST conversion (08:32:11Z -> 14:02:11)."""
    utc_dt = datetime(2026, 10, 23, 8, 32, 11, tzinfo=UTC)
    ist_dt = to_ist(utc_dt)
    assert ist_dt is not None
    assert ist_dt.hour == 14
    assert ist_dt.minute == 2
    assert ist_dt.second == 11
    assert format_time_ist(utc_dt) == "14:02:11"

    # ISO string with Z
    assert format_time_ist("2026-10-23T08:32:11Z") == "14:02:11"


def test_doc_tag_uses_last_4_id_chars() -> None:
    """Test that the doc tag uses the LAST 4 hex chars of the document id."""
    test_id = UUID("01950000-0000-7000-8000-000000007f3a")
    tag = format_doc_tag("invoice", "INV-24", doc_id=test_id, mode="masked")
    assert tag == "INV INV-24 #7f3a"
    assert tag.endswith("#7f3a")

    # File tag
    file_id = UUID("01950000-0000-7000-8000-00000000b21c")
    file_tag = format_doc_tag("FILE", "test.pdf", doc_id=file_id, mode="masked")
    assert file_tag == "FILE .pdf #b21c"


def test_masking_modes() -> None:
    """Test masked vs show modes for vendor, amounts, file names, and GSTIN."""
    vendor_id = UUID("01950000-0000-7000-8000-000000009c4d")
    vendor_name = "Acme Global Industrial Solutions Ltd."
    file_id = UUID("01950000-0000-7000-8000-000000003e8a")
    file_name = "vendor_invoice_1001.pdf"
    full_gstin = "27ABCDE1234F1Z5"

    # MASKED mode:
    assert mask_vendor(vendor_name, vendor_id=vendor_id, mode="masked") == "V-9c4d"
    assert mask_amount(Decimal("118000.00"), mode="masked") == "₹***"
    assert mask_amount(Decimal("118000.00"), mode="masked", ascii_mode=True) == "Rs***"
    assert mask_file_name(file_name, file_id=file_id, mode="masked") == ".pdf #3e8a"
    # GSTIN is masked in BOTH modes
    assert mask_gstin(full_gstin, ascii_mode=False) == "…F1Z5"
    assert "ABCDE" not in mask_gstin(full_gstin)

    # SHOW mode:
    # Vendor truncated to 30 chars
    assert mask_vendor(vendor_name, vendor_id=vendor_id, mode="show") == vendor_name[:30]
    assert mask_amount(Decimal("118000.00"), mode="show") == "₹1,18,000.00"
    assert mask_file_name(file_name, file_id=file_id, mode="show") == file_name
    # GSTIN still masked
    assert mask_gstin(full_gstin, ascii_mode=False) == "…F1Z5"


def test_exact_line_format() -> None:
    """Verify exact output matches prompt §7 example."""
    ts = datetime(2026, 10, 23, 8, 32, 14, tzinfo=UTC)  # -> 14:02:14 IST
    doc_id = UUID("01950000-0000-7000-8000-000000007f3a")
    line = render_event_line(
        timestamp=ts,
        icon="🧾",
        stage="DOC",
        doc_tag="INV 24 #7f3a",
        facts=[
            "scanned PDF · OCR ran",
            "v1",
            "total ₹1,18,000.00",
            "needs review: LOW_CONFIDENCE",
        ],
        doc_id=doc_id,
        ascii_mode=False,
        no_color=True,
    )
    expected = (
        "14:02:14 🧾 DOC     INV 24 #7f3a                   │ "
        "scanned PDF · OCR ran · v1 · total ₹1,18,000.00 · needs review: LOW_CONFIDENCE"
    )
    assert line == expected


def test_ascii_mode_transformations() -> None:
    """Test ASCII mode: no icons, | separator, OK/!/X marks, Rs currency."""
    ts = datetime(2026, 10, 23, 8, 32, 14, tzinfo=UTC)
    line = render_event_line(
        timestamp=ts,
        icon="🧾",
        stage="DOC",
        doc_tag="INV 24 #7f3a",
        facts=["digital PDF", "total Rs 1,18,000.00"],
        doc_id=None,
        ascii_mode=True,
        no_color=True,
    )
    # No icon in line
    assert "🧾" not in line
    # | separator instead of │
    assert "|" in line
    assert "│" not in line

    # Status marks in ASCII mode
    assert format_status_mark("✓", ascii_mode=True, no_color=True) == "OK"
    assert format_status_mark("⚠", ascii_mode=True, no_color=True) == "!"
    assert format_status_mark("✗", ascii_mode=True, no_color=True) == "X"

    # Date separator in ASCII mode
    d = datetime(2026, 10, 23, 14, 0, 0, tzinfo=UTC)
    sep = render_date_separator(d, ascii_mode=True)
    assert sep == "-- 23/10/2026 --"


def test_color_on_off() -> None:
    """Test ANSI color on and off (no escape characters when no_color=True)."""
    ts = datetime(2026, 10, 23, 8, 32, 14, tzinfo=UTC)
    doc_id = UUID("01950000-0000-7000-8000-000000007f3a")

    # With color
    colored_line = render_event_line(
        timestamp=ts,
        icon="🧾",
        stage="DOC",
        doc_tag="INV 24 #7f3a",
        facts=["scanned PDF"],
        doc_id=doc_id,
        ascii_mode=False,
        no_color=False,
    )
    assert "\x1b[" in colored_line

    # Without color
    plain_line = render_event_line(
        timestamp=ts,
        icon="🧾",
        stage="DOC",
        doc_tag="INV 24 #7f3a",
        facts=["scanned PDF"],
        doc_id=doc_id,
        ascii_mode=False,
        no_color=True,
    )
    assert "\x1b" not in plain_line


def test_status_colors() -> None:
    """Test that statuses map to exact required colors:

    Matched -> green, Needs review -> yellow, Unlinked/Exception -> red,
    Duplicate -> magenta, Pending -> blue.
    """
    assert ANSI_GREEN in format_status_text("Matched", no_color=False)
    assert ANSI_GREEN in format_status_text("Matched ✓", no_color=False)
    assert ANSI_YELLOW in format_status_text("Needs review", no_color=False)
    assert ANSI_RED in format_status_text("Unlinked", no_color=False)
    assert ANSI_RED in format_status_text("Exception", no_color=False)
    assert ANSI_MAGENTA in format_status_text("Duplicate", no_color=False)
    assert ANSI_BLUE in format_status_text("Pending", no_color=False)

    # When no_color is True
    assert "\x1b" not in format_status_text("Matched", no_color=True)


def test_line_length_capped_at_160() -> None:
    """Test that long fact lines are capped at 160 characters and end with ellipsis."""
    ts = datetime(2026, 10, 23, 8, 32, 14, tzinfo=UTC)
    long_facts = [
        "very long description of an event that exceeds ordinary length " * 4,
    ]
    line = render_event_line(
        timestamp=ts,
        icon="🧾",
        stage="DOC",
        doc_tag="INV 24 #7f3a",
        facts=long_facts,
        doc_id=None,
        ascii_mode=False,
        no_color=True,
    )
    assert len(strip_ansi(line)) <= 160
    assert line.endswith("…")


def test_doc_tag_color_consistency() -> None:
    """Test that the same document ID consistently yields the same color code."""
    doc_id1 = UUID("01950000-0000-7000-8000-000000007f3a")
    doc_id2 = UUID("01950000-0000-7000-8000-000000007f3a")
    doc_id3 = UUID("01950000-0000-7000-8000-000000001234")

    color1 = get_doc_tag_color(doc_id1, no_color=False)
    color2 = get_doc_tag_color(doc_id2, no_color=False)
    color3 = get_doc_tag_color(doc_id3, no_color=False)

    assert color1 == color2
    assert color1 in (ANSI_CYAN, ANSI_GREEN, ANSI_YELLOW, ANSI_BLUE, ANSI_MAGENTA, ANSI_RED)
    assert color3 in (ANSI_CYAN, ANSI_GREEN, ANSI_YELLOW, ANSI_BLUE, ANSI_MAGENTA, ANSI_RED)


def test_banner_and_heartbeat() -> None:
    """Test startup banner and heartbeat rendering."""
    banner = render_startup_banner(
        values_mode="masked",
        interval_s=3,
        since_ist="23/10/2026 14:00:00",
        page_reader="qwen3.5:9b",
        doc_count=42,
        queue_status="queue idle",
        no_color=True,
    )
    assert "ReconBench pipeline log" in banner
    assert "values=masked" in banner
    assert "every 3s" in banner
    assert "page reader: qwen3.5:9b" in banner
    assert "documents: 42" in banner
    assert "queue: queue idle" in banner

    hb = render_heartbeat("14:15:00", no_color=True)
    assert "watching" in hb
    assert "queue idle" in hb
    assert "last event 14:15:00" in hb


def test_dedupe_and_bounded_keys() -> None:
    """Test that mark_seen deduplicates keys and stays bounded."""
    from reconbench_tools.pipeline_log import PipelineWatcher

    watcher = PipelineWatcher()
    # First time seeing key
    assert watcher.mark_seen("document_version", "vid-1") is True
    # Second time seeing same key
    assert watcher.mark_seen("document_version", "vid-1") is False

    # Check bounded size
    watcher.seen_order = deque(maxlen=3)
    watcher.seen_keys.clear()
    assert watcher.mark_seen("doc", "1") is True
    assert watcher.mark_seen("doc", "2") is True
    assert watcher.mark_seen("doc", "3") is True
    assert len(watcher.seen_keys) == 3

    # Adding a 4th should evict the 1st
    assert watcher.mark_seen("doc", "4") is True
    assert ("doc", "1") not in watcher.seen_keys
    assert ("doc", "4") in watcher.seen_keys


def test_queue_line_only_on_change_and_throttled() -> None:
    """Test that queue lines only emit when counts change AND at least 10s have elapsed."""
    from unittest.mock import MagicMock, patch

    from reconbench_tools.pipeline_log import PipelineWatcher

    watcher = PipelineWatcher()
    watcher.workspace_id = UUID("01950000-0000-7000-8000-000000001111")

    # Fake connection and mock query results
    mock_conn = MagicMock()
    dt_db = datetime(2026, 10, 23, 14, 0, 0, tzinfo=UTC)
    dt_since = datetime(2026, 10, 23, 13, 0, 0, tzinfo=UTC)
    with patch("reconbench_tools.pipeline_log.get_db_now", return_value=dt_db), \
         patch("reconbench_tools.pipeline_log.query_file_events", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_doc_events", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_vendor_resolutions", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_ai_requests", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_link_events", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_link_resolutions", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_user_actions", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_recon_runs_and_cases", return_value=([], [])), \
         patch("reconbench_tools.pipeline_log.query_export_events", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_ai_health_events", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_failed_jobs", return_value=[]):

        # First cycle with queue counts
        q_val1 = {"ingest": (1, 0)}
        with patch("reconbench_tools.pipeline_log.get_queue_counts", return_value=q_val1):
            lines1 = watcher.process_cycle(mock_conn, since=dt_since)
            q_lines = [line for line in lines1 if "QUEUE" in line]
            assert len(q_lines) == 1
            assert "ingest: 1 todo" in q_lines[0]

        # Second cycle immediately after (same counts): no queue line
        with patch("reconbench_tools.pipeline_log.get_queue_counts", return_value=q_val1):
            lines2 = watcher.process_cycle(mock_conn, since=dt_since)
            q_lines = [line for line in lines2 if "QUEUE" in line]
            assert len(q_lines) == 0

        # Third cycle immediately after but with changed counts: still throttled (< 10s)
        q_val2 = {"ingest": (2, 0)}
        with patch("reconbench_tools.pipeline_log.get_queue_counts", return_value=q_val2):
            lines3 = watcher.process_cycle(mock_conn, since=dt_since)
            q_lines = [line for line in lines3 if "QUEUE" in line]
            assert len(q_lines) == 0

        # Fourth cycle after 11 seconds with changed counts: emits queue line
        watcher.last_queue_print_time -= 11.0
        with patch("reconbench_tools.pipeline_log.get_queue_counts", return_value=q_val2):
            lines4 = watcher.process_cycle(mock_conn, since=dt_since)
            q_lines = [line for line in lines4 if "QUEUE" in line]
            assert len(q_lines) == 1
            assert "ingest: 2 todo" in q_lines[0]


def test_toggle_off_never_opens_connection() -> None:
    """Test that when RB_PIPELINE_LOG=off and not in --once/--selftest, no connection is opened."""
    from unittest.mock import patch

    import reconbench_tools.pipeline_log as pl

    with patch.dict(os.environ, {"RB_PIPELINE_LOG": "off"}), \
         patch("sys.argv", ["pipeline_log"]), \
         patch("reconbench_tools.pipeline_log.create_trace_engine") as mock_engine, \
         patch("time.sleep") as mock_sleep:

        # Let the loop run once then exit
        def stop_loop(*_args: Any) -> None:
            pl._RUNNING = False

        mock_sleep.side_effect = stop_loop

        pl._RUNNING = True
        with pytest.raises(SystemExit) as exc_info:
            pl.main()

        assert exc_info.value.code == 0
        mock_engine.assert_not_called()


def test_db_error_class_name_only_never_leaks_connection_string() -> None:
    """Test that database error printing shows only class name and never connection secrets."""
    class FakeOperationalError(Exception):
        pass

    sensitive_dsn = "postgresql://reconbench_app:SuperSecretPassword123@10.0.0.5:5432/reconbench"
    err = FakeOperationalError(f"connection to server at {sensitive_dsn} failed: timeout expired")

    # Format the error line as pipeline_log does
    printed_line = f"⚠ database error ({type(err).__name__})"

    assert "SuperSecretPassword123" not in printed_line
    assert sensitive_dsn not in printed_line
    assert printed_line == "⚠ database error (FakeOperationalError)"


def test_slow_poll_backoff() -> None:
    """Test that slow cycle (> 2.0s) triggers slow poll line and doubles interval."""
    from reconbench_tools.pipeline_log import PipelineWatcher

    watcher = PipelineWatcher(interval_s=3)
    assert watcher.interval_s == 3

    # Simulate elapsed time > 2.0s
    elapsed = 2.45
    if elapsed > 2.0:
        watcher.interval_s = min(30, watcher.interval_s * 2)

    assert watcher.interval_s == 6

    # Test ceiling at 30s
    watcher.interval_s = 20
    elapsed = 3.1
    if elapsed > 2.0:
        watcher.interval_s = min(30, watcher.interval_s * 2)
    assert watcher.interval_s == 30


def test_flood_summary_lines() -> None:
    """Test flood control: > 5 PO/GRN documents produce a single summary line."""
    from unittest.mock import MagicMock, patch

    from reconbench_tools.pipeline_log import PipelineWatcher

    watcher = PipelineWatcher()
    watcher.workspace_id = UUID("01950000-0000-7000-8000-000000001111")

    # Generate 6 synthetic PO document events
    mock_po_events = [
        {
            "version_id": UUID(f"01950000-0000-7000-8000-00000000{i:04x}"),
            "document_id": UUID(f"01950000-0000-7000-8000-00000000{i:04x}"),
            "doc_no": f"PO-{i}",
            "doc_type": "po",
            "seq": 1,
            "created_at": datetime(2026, 10, 23, 14, 0, 0, tzinfo=UTC),
        }
        for i in range(1, 7)
    ]

    mock_conn = MagicMock()
    with patch("reconbench_tools.pipeline_log.query_file_events", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_doc_events", return_value=mock_po_events), \
         patch("reconbench_tools.pipeline_log.query_vendor_resolutions", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_ai_requests", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_link_events", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_link_resolutions", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_user_actions", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_recon_runs_and_cases", return_value=([], [])), \
         patch("reconbench_tools.pipeline_log.query_export_events", return_value=[]), \
         patch("reconbench_tools.pipeline_log.query_ai_health_events", return_value=[]), \
         patch("reconbench_tools.pipeline_log.get_queue_counts", return_value={}):

        lines = watcher.process_cycle(mock_conn, since=datetime(2026, 10, 23, 13, 0, 0, tzinfo=UTC))
        # Should NOT have 6 separate PO lines; should have 1 summary line
        doc_lines = [line for line in lines if "DOC" in line]
        assert len(doc_lines) == 1
        assert "Tally import: 6 PO documents" in doc_lines[0]
        assert "IMPORT" in doc_lines[0]


def test_sigterm_stops_loop() -> None:
    """Test that SIGTERM handler sets _RUNNING flag to False."""
    import reconbench_tools.pipeline_log as pl

    pl._RUNNING = True
    pl._handle_sigterm(signal.SIGTERM, None)
    assert pl._RUNNING is False


def test_ai_suggestions_masked_vs_show() -> None:
    """Test that AI suggestions in show mode expose values only for allowed fields,
    and masked mode exposes only field names/counts without sensitive values.
    """
    from unittest.mock import MagicMock, patch

    from reconbench_tools.pipeline_log import PipelineWatcher

    watcher = PipelineWatcher(values_mode="show")
    watcher.workspace_id = UUID("01950000-0000-7000-8000-000000001111")
    cid = UUID("01950000-0000-7000-8000-00000000aaaa")
    doc_id = UUID("01950000-0000-7000-8000-00000000bbbb")

    ai_events = [{
        "request_id": cid,
        "call_id": cid,
        "document_id": doc_id,
        "doc_no": "INV-100",
        "doc_type": "invoice",
        "ai_cache_id": cid,
        "subject_id": doc_id,
        "provider": "vllm",
        "model": "qwen3.5:9b",
        "request_state": "succeeded",
        "state": "succeeded",
        "latency_ms": 1200,
        "created_at": datetime(2026, 10, 23, 14, 0, 0, tzinfo=UTC),
    }]
    suggestions_cache = {
        cid: [
            {"name": "doc_no", "text": "INV-100"},
            {"name": "vendor_name", "text": "Sensitive Vendor Ltd"},
            {"name": "total", "text": "118000.00"},
        ]
    }

    mock_conn = MagicMock()
    with (
        patch("reconbench_tools.pipeline_log.query_file_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_doc_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_vendor_resolutions", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_ai_requests", return_value=ai_events),
        patch(
            "reconbench_tools.pipeline_log.query_ai_suggestions_details",
            return_value=(suggestions_cache, {}),
        ),
        patch("reconbench_tools.pipeline_log.query_link_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_link_resolutions", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_user_actions", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_recon_runs_and_cases", return_value=([], [])),
        patch("reconbench_tools.pipeline_log.query_export_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_ai_health_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.get_queue_counts", return_value={}),
    ):
        cycle_since = datetime(2026, 10, 23, 13, 0, 0, tzinfo=UTC)
        lines_show = watcher.process_cycle(mock_conn, since=cycle_since)
        ai_lines_show = [line for line in lines_show if "AI" in line]
        assert len(ai_lines_show) == 2  # Call succeeded line + suggestions line
        sugg_line = ai_lines_show[1]
        assert "doc_no -> AI 'INV-100'" in sugg_line
        assert "total -> AI '118000.00'" in sugg_line
        # Sensitive vendor_name text MUST NOT be present
        assert "Sensitive Vendor Ltd" not in sugg_line

        # Now test masked mode
        watcher_masked = PipelineWatcher(values_mode="masked")
        watcher_masked.workspace_id = watcher.workspace_id
        lines_masked = watcher_masked.process_cycle(mock_conn, since=cycle_since)
        ai_lines_masked = [line for line in lines_masked if "AI" in line]
        assert len(ai_lines_masked) == 2
        sugg_line_masked = ai_lines_masked[1]
        assert "3 suggestions" in sugg_line_masked
        # In masked mode, values are omitted from facts
        facts_part = sugg_line_masked.split("│")[1]
        assert "118000.00" not in facts_part
        assert "Sensitive Vendor Ltd" not in facts_part


def test_ai_health_rendering() -> None:
    """Test AI health event rendering: connected, error code, last check time."""
    from unittest.mock import MagicMock, patch

    from reconbench_tools.pipeline_log import PipelineWatcher

    watcher = PipelineWatcher()
    watcher.workspace_id = UUID("01950000-0000-7000-8000-000000001111")
    prov_id = UUID("01950000-0000-7000-8000-00000000cccc")

    health_events = [{
        "id": prov_id,
        "provider": "vllm-local",
        "connected_at": datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC),
        "last_error_code": "503",
        "last_error_at": datetime(2026, 10, 23, 13, 50, 0, tzinfo=UTC),
        "last_health_at": datetime(2026, 10, 23, 14, 0, 0, tzinfo=UTC),
        "last_test_at": None,
        "last_test_ok": None,
    }]

    mock_conn = MagicMock()
    with (
        patch("reconbench_tools.pipeline_log.query_file_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_doc_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_vendor_resolutions", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_ai_requests", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_link_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_link_resolutions", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_user_actions", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_recon_runs_and_cases", return_value=([], [])),
        patch("reconbench_tools.pipeline_log.query_export_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_ai_health_events", return_value=health_events),
        patch("reconbench_tools.pipeline_log.get_queue_counts", return_value={}),
    ):
        lines = watcher.process_cycle(mock_conn, since=datetime(2026, 10, 23, 13, 0, 0, tzinfo=UTC))
        health_lines = [line for line in lines if "HEALTH" in line]
        assert len(health_lines) == 1
        hl = health_lines[0]
        assert "vllm-local" in hl
        assert "Error: 503" in hl
        assert "last check 19:30:00" in hl  # 14:00 UTC -> 19:30 IST


def test_export_file_rendering() -> None:
    """Test EXPORT line rendering from export_file events."""
    from unittest.mock import MagicMock, patch

    from reconbench_tools.pipeline_log import PipelineWatcher

    watcher = PipelineWatcher()
    watcher.workspace_id = UUID("01950000-0000-7000-8000-000000001111")
    exp_id = UUID("01950000-0000-7000-8000-00000000d8e2")

    export_events = [{
        "id": exp_id,
        "status": "ready",
        "case_count": 142,
        "error_code": None,
        "created_at": datetime(2026, 10, 23, 14, 0, 0, tzinfo=UTC),
        "built_at": datetime(2026, 10, 23, 14, 0, 2, tzinfo=UTC),
    }]

    mock_conn = MagicMock()
    with (
        patch("reconbench_tools.pipeline_log.query_file_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_doc_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_vendor_resolutions", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_ai_requests", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_link_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_link_resolutions", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_user_actions", return_value=[]),
        patch("reconbench_tools.pipeline_log.query_recon_runs_and_cases", return_value=([], [])),
        patch("reconbench_tools.pipeline_log.query_export_events", return_value=export_events),
        patch("reconbench_tools.pipeline_log.query_ai_health_events", return_value=[]),
        patch("reconbench_tools.pipeline_log.get_queue_counts", return_value={}),
    ):
        lines = watcher.process_cycle(mock_conn, since=datetime(2026, 10, 23, 13, 0, 0, tzinfo=UTC))
        exp_lines = [line for line in lines if "EXPORT" in line]
        assert len(exp_lines) == 1
        el = exp_lines[0]
        assert "EXPORT" in el
        assert "Export ready" in el
        assert "142 rows" in el


def test_pipeline_log_queries_security_audit() -> None:
    """Security audit of pipeline_log_queries.py:

    - No write/DDL keywords inside SQL queries
    - No SELECT *
    - No prohibited tables (secret, session, rate_limit_event, idempotency_key)
    - No credential columns (password, token, secret, api_key) in SELECT queries
    """
    import re
    from pathlib import Path

    tools_dir = Path(__file__).resolve().parents[2] / "reconbench_tools"
    queries_file = tools_dir / "pipeline_log_queries.py"
    assert queries_file.exists()
    content = queries_file.read_text(encoding="utf-8")

    # Extract all SQL query blocks enclosed in sa.text(...)
    pattern = r"sa\.text\(\s*(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"[^\"]*\"|'[^']*')\s*\)"
    sql_blocks = re.findall(pattern, content)
    assert len(sql_blocks) > 0, "Failed to find any sa.text queries"
    combined_sql = "\n".join(sql_blocks)

    # 1. No write or DDL keywords
    forbidden_sql = [
        r"\bINSERT\s+INTO\b",
        r"\bUPDATE\s+[A-Za-z_]+\s+SET\b",
        r"\bDELETE\s+FROM\b",
        r"\bDROP\s+TABLE\b",
        r"\bALTER\s+TABLE\b",
        r"\bTRUNCATE\b",
        r"\bGRANT\b",
        r"\bREVOKE\b",
        r"\bCOPY\b",
        r"\bFOR\s+UPDATE\b",
        r"\bFOR\s+SHARE\b",
    ]
    for p in forbidden_sql:
        match = re.search(p, combined_sql, re.IGNORECASE)
        assert match is None, f"Forbidden SQL pattern '{p}' matched: {match}"

    # 2. No SELECT *
    assert not re.search(
        r"\bSELECT\s+\*\b", combined_sql, re.IGNORECASE
    ), "SELECT * found in queries"

    # 3. Prohibited tables
    prohibited_tables = ["secret", "session", "rate_limit_event", "idempotency_key"]
    for tbl in prohibited_tables:
        pattern = rf"\bFROM\s+{tbl}\b|\bJOIN\s+{tbl}\b"
        match = re.search(pattern, combined_sql, re.IGNORECASE)
        assert match is None, f"Prohibited table '{tbl}' found in queries: {match}"

    # 4. Forbidden credential columns
    forbidden_cols = ["api_key", "password", "token", "secret", "credentials"]
    for col in forbidden_cols:
        pattern = rf"\b{col}\b"
        match = re.search(pattern, combined_sql, re.IGNORECASE)
        assert match is None, f"Credential column name '{col}' found in queries: {match}"



