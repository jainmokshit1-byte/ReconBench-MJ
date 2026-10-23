"""Formatting utilities for the ReconBench terminal trace tool.

Supports ANSI colours in PowerShell / Windows Terminal, status marks (✓ ✗ ⏳ ⚠),
aligned tabular layouts, and --mask for sensitive fields.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

# Timezone: IST (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# ANSI escape codes
_ESC = "\033["
GREEN = f"{_ESC}92m"
RED = f"{_ESC}91m"
YELLOW = f"{_ESC}93m"
CYAN = f"{_ESC}96m"
BOLD = f"{_ESC}1m"
DIM = f"{_ESC}90m"
RESET = f"{_ESC}0m"


def format_time(ts: Any) -> str:
    """Format a timestamp in IST with the date (e.g. 22 Oct 11:49 IST)."""
    if ts is None:
        return "not recorded"
    dt: datetime | None = None
    if isinstance(ts, datetime):
        dt = ts
    elif isinstance(ts, date):
        return f"{ts.day} {ts.strftime('%b %Y')}"
    else:
        s = str(ts).strip()
        try:
            # Handle ISO or SQL timestamp strings
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return s[:19]

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_ist = dt.astimezone(IST)
    return f"{dt_ist.day} {dt_ist.strftime('%b %H:%M')} IST"


def normalize_date_str(val: Any) -> str | None:
    """Normalize date strings or objects to YYYY-MM-DD for comparison."""
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s or s in ("—", "-", "not recorded"):
        return None

    formats = [
        "%Y-%m-%d",
        "%d-%b-%y",
        "%d-%b-%Y",
        "%d-%B-%y",
        "%d-%B-%Y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d.%m.%Y",
        "%d.%m.%y",
        "%Y/%m/%d",
        "%b %d, %Y",
        "%d %b %Y",
        "%d %b %y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def is_po_or_grn_number(text: str) -> bool:
    """Check if handwritten ref text matches a PO or GRN number pattern."""
    clean = text.strip()
    try:
        from reconbench_core.extraction.handwritten_refs import refs_from_annotation
        typed = refs_from_annotation(clean, looks_like="po_number", doc_date=None)
        if typed and any(r.normalised is not None for r in typed):
            return True
        typed_grn = refs_from_annotation(clean, looks_like="grn_number", doc_date=None)
        if typed_grn and any(r.normalised is not None for r in typed_grn):
            return True
    except Exception:
        pass

    # Fallback pattern checks:
    # Must have digits, dashes/slashes, no purely alphabetic words
    if re.search(r"\b(?:PO|GRN)[/-]?[A-Za-z0-9/_-]*\d+[A-Za-z0-9/_-]*\b", clean, re.IGNORECASE):
        return True
    if re.search(r"^\d{2}-\d{2,4}$", clean):
        return True
    if re.search(r"^\d{2}/\d{2}-\d{2,4}$", clean):
        return True
    if re.search(r"^\d{2}-\d{2}/\d{2}-\d{2,4}$", clean):
        return True
    return False


def enable_windows_ansi() -> None:
    """Enable VT100 / ANSI escape processing on Windows console if available."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            # -11 = STD_OUTPUT_HANDLE
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass


def should_color(no_color_flag: bool = False) -> bool:
    """Return True if ANSI colours should be emitted."""
    if no_color_flag or bool(os.environ.get("NO_COLOR")):
        return False
    # If stdout is a TTY or in Windows Terminal / modern console
    return sys.stdout.isatty() or bool(os.environ.get("WT_SESSION"))


def colorize(text: str, color_code: str, no_color: bool = False) -> str:
    """Wrap text in ANSI color codes unless no_color is True."""
    if no_color or not text:
        return text
    return f"{color_code}{text}{RESET}"


def mark_ok(no_color: bool = False) -> str:
    return colorize("✓", GREEN, no_color=no_color)


def mark_fail(no_color: bool = False) -> str:
    return colorize("✗", RED, no_color=no_color)


def mark_pending(no_color: bool = False) -> str:
    return colorize("⏳", YELLOW, no_color=no_color)


def mark_warn(no_color: bool = False) -> str:
    return colorize("⚠", YELLOW, no_color=no_color)


# --- Masking Helpers ---


def mask_gstin(gstin: str | None, mask: bool = False) -> str:
    """Format or mask a GSTIN."""
    if not gstin:
        return "not recorded"
    if not mask:
        return gstin
    s = str(gstin).strip()
    if len(s) == 15:
        return f"{s[:2]}***********{s[-2:]}"
    return "[MASKED GSTIN]"


def mask_vendor(name: str | None, mask: bool = False) -> str:
    """Format or mask a vendor name."""
    if not name:
        return "not recorded"
    if not mask:
        return name
    return "[MASKED VENDOR]"


def mask_amount(value: Any, mask: bool = False) -> str:
    """Format or mask a monetary amount or rate."""
    if value is None:
        return "not recorded"
    if mask:
        return "₹***.**"
    try:
        dec = Decimal(str(value))
        return f"{dec:,.2f}"
    except Exception:
        return str(value)


def mask_qty(value: Any, mask: bool = False) -> str:
    """Format quantity (quantities are not masked by default unless requested)."""
    if value is None:
        return "not recorded"
    try:
        dec = Decimal(str(value))
        if dec == dec.to_integral():
            return str(int(dec))
        return f"{dec:,.4f}".rstrip("0").rstrip(".")
    except Exception:
        return str(value)


# --- Stage Formatters ---


def format_doc_header(doc: dict[str, Any], mask: bool = False, no_color: bool = False) -> str:
    """Format DOCUMENT line:

    DOCUMENT  INV-2026-018 · invoice · vendor Aster Steelworks (GSTIN rule, 1.00)
    """
    doc_no = doc.get("doc_no") or "not recorded"
    doc_type = doc.get("doc_type") or "unknown"
    vendor_name = mask_vendor(doc.get("vendor_name"), mask=mask)
    rule = doc.get("vendor_rule") or "none"
    score = doc.get("vendor_score")
    score_str = f"{score:.2f}" if score is not None else "1.00" if rule == "gstin" else "—"

    rule_desc = f"{rule} rule, {score_str}"
    header_title = colorize("DOCUMENT", BOLD, no_color=no_color)
    doc_no_col = colorize(str(doc_no), BOLD, no_color=no_color)
    return f"{header_title}  {doc_no_col} · {doc_type} · vendor {vendor_name} ({rule_desc})"


def format_ingestion_line(
    ingest: dict[str, Any], no_color: bool = False
) -> str:
    """Format INGESTION line:

    INGESTION ✓ uploaded 10:40:58 · parsed 10:41:30 · PDF OCR · 2 lines
    """
    title = colorize("INGESTION", BOLD, no_color=no_color)
    status = ingest.get("status", "unknown")
    if status in ("parsed", "succeeded", "ok"):
        m = mark_ok(no_color)
    elif status in ("parsing", "queued", "received"):
        m = mark_pending(no_color)
    else:
        m = mark_fail(no_color)

    uploaded = (
        format_time(ingest.get("uploaded_at"))
        if ingest.get("uploaded_at")
        else "not recorded"
    )
    parsed = (
        format_time(ingest.get("parse_finished_at") or ingest.get("parse_started_at"))
        if (ingest.get("parse_finished_at") or ingest.get("parse_started_at"))
        else "not recorded"
    )
    method = ingest.get("extraction_method") or ingest.get("format") or "unknown"
    method_display = {
        "pdf_ocr": "PDF OCR",
        "pdf_layout": "PDF layout",
        "ocr": "OCR",
        "tally_native.v1": "Tally XML",
        "xml": "XML",
        "csv": "CSV",
        "manual": "manual correction",
    }.get(method.lower(), method)

    line_count = ingest.get("line_count")
    lines_str = f"{line_count} line{'s' if line_count != 1 else ''}" if line_count is not None else "lines not recorded"

    return f"{title} {m} uploaded {uploaded} · parsed {parsed} · {method_display} · {lines_str}"


def format_ai_reader_line(
    ai_info: dict[str, Any] | None, no_color: bool = False
) -> str:
    """Format AI READER line:

    AI READER ✓ vllm/qwen3.5:9b · succeeded · 84 s · 16 suggestions · handwritten refs: PO "08-136", GRN "08-124"
    """
    title = colorize("AI READER", BOLD, no_color=no_color)
    if not ai_info:
        return f"{title} — not recorded (no AI reading for this document)"

    state = ai_info.get("state") or ai_info.get("outcome") or "unknown"
    provider = ai_info.get("provider") or "ai"
    model = ai_info.get("model") or "model"
    prov_model = f"{provider}/{model}"

    if state in ("succeeded", "ok", "applied"):
        m = mark_ok(no_color)
        status_text = "succeeded"
    elif state in ("queued", "pending", "reserved", "sending"):
        m = mark_pending(no_color)
        status_text = state
    else:
        m = mark_fail(no_color)
        status_text = ai_info.get("error_code") or ai_info.get("skip_code") or state

    latency_ms = ai_info.get("latency_ms")
    latency_str = f"{round(latency_ms / 1000)} s" if latency_ms is not None else None

    sugg_count = ai_info.get("suggestion_count", 0)
    sugg_str = f"{sugg_count} suggestion{'s' if sugg_count != 1 else ''}"

    refs = ai_info.get("handwritten_refs", [])
    if refs:
        formatted_refs = []
        for r in refs:
            rtype = r.get("type", "ref")
            rtext = r.get("text", "")
            if is_po_or_grn_number(rtext):
                formatted_refs.append(f'{rtype} "{rtext}"')
            else:
                formatted_refs.append(f'{rtype} "{rtext}" (not a number)')
        refs_str = "handwritten refs: " + ", ".join(formatted_refs)
    else:
        refs_str = None

    parts = [prov_model, status_text]
    if latency_str:
        parts.append(latency_str)
    parts.append(sugg_str)
    if refs_str:
        parts.append(refs_str)

    return f"{title} {m} " + " · ".join(parts)


def format_fields_table(
    fields: Sequence[dict[str, Any]], mask: bool = False, no_color: bool = False
) -> list[str]:
    """Format FIELD comparison table:

    FIELD        STORED (source, conf)        AI SUGGESTION        MARK
    invoice no   24 (OCR, 0.62)               OSC/26-27/3877       ⚠ differs
    total        10,030.00 (OCR, 0.97)        10,030.00            ✓
    """
    lines: list[str] = []
    header = f"{'FIELD':<12} {'STORED (source, conf)':<28} {'AI SUGGESTION':<20} {'MARK'}"
    lines.append(colorize(header, BOLD, no_color=no_color))

    for f in fields:
        name = f.get("name", "")
        stored_val = f.get("stored_val")
        source = f.get("source") or "rules"
        conf = f.get("conf")
        conf_str = f"{conf:.2f}" if conf is not None else "—"

        is_amount = any(k in name.lower() for k in ("total", "amount", "taxable", "cgst", "sgst", "igst"))
        is_gstin = "gstin" in name.lower()

        if is_amount:
            disp_stored = mask_amount(stored_val, mask=mask)
        elif is_gstin:
            disp_stored = mask_gstin(stored_val, mask=mask)
        else:
            disp_stored = str(stored_val) if stored_val is not None else "not recorded"

        stored_part = f"{disp_stored} ({source}, {conf_str})"

        ai_val = f.get("ai_val")
        if is_amount:
            disp_ai = mask_amount(ai_val, mask=mask) if ai_val is not None else "—"
        elif is_gstin:
            disp_ai = mask_gstin(ai_val, mask=mask) if ai_val is not None else "—"
        else:
            disp_ai = str(ai_val) if ai_val is not None else "—"

        # Compare values
        if ai_val is None or stored_val is None:
            mark_str = "—"
        else:
            is_date = "date" in name.lower()
            d1 = normalize_date_str(stored_val) if is_date else None
            d2 = normalize_date_str(ai_val) if is_date else None

            if is_date and d1 and d2:
                if d1 == d2:
                    mark_str = mark_ok(no_color)
                else:
                    diff_label = colorize("⚠ differs", YELLOW, no_color=no_color)
                    mark_str = diff_label
            else:
                # Normalize for comparison
                s1 = re.sub(r"[,\s₹Rs.]", "", str(stored_val)).strip().lower()
                s2 = re.sub(r"[,\s₹Rs.]", "", str(ai_val)).strip().lower()
                if s1 == s2:
                    mark_str = mark_ok(no_color)
                else:
                    diff_label = colorize("⚠ differs", YELLOW, no_color=no_color)
                    mark_str = diff_label

        lines.append(f"{name:<12} {stored_part:<28} {disp_ai:<20} {mark_str}")

    return lines


def format_lines_table(
    doc_lines: Sequence[dict[str, Any]], mask: bool = False, no_color: bool = False
) -> list[str]:
    """Format LINES table:

    LINES        #  description              qty   rate     amount
                 1  500 GB SSD ...            1    8,500   8,500
    """
    out: list[str] = []
    header = f"{'LINES':<12} {'#':<2} {'description':<26} {'qty':<6} {'rate':<9} {'amount':<9}"
    out.append(colorize(header, BOLD, no_color=no_color))

    if not doc_lines:
        out.append(f"{'':<12} (no lines extracted / not recorded)")
        return out

    for row in doc_lines:
        no = str(row.get("line_no", ""))
        desc = str(row.get("description") or "not recorded")
        if len(desc) > 25:
            desc = desc[:22] + "..."
        qty = mask_qty(row.get("quantity"))
        rate = mask_amount(row.get("rate"), mask=mask)
        amount = mask_amount(row.get("amount"), mask=mask)
        out.append(f"{'':<12} {no:<2} {desc:<26} {qty:<6} {rate:<9} {amount:<9}")

    return out


def format_links_section(
    links: Sequence[dict[str, Any]],
    grn_links: Sequence[dict[str, Any]] | None = None,
    no_color: bool = False,
) -> list[str]:
    """Format LINKS section:

    LINKS        line 1 → PO PO/26-27/08-227 line 1 · confirmed · 0.88 · content
                 GRN GRN/BCSL/25-26/08-210 header → PO PO/26-27/08-227 · auto · 0.99
    """
    out: list[str] = []
    prefix = colorize("LINKS", BOLD, no_color=no_color)
    indent = " " * 12

    all_links = list(links) + (list(grn_links) if grn_links else [])
    if not all_links:
        out.append(f"{prefix}        (no links found / unlinked)")
        return out

    # Deduplicate links so the same link is not printed twice
    seen_keys: set[str] = set()
    deduped_links: list[dict[str, Any]] = []
    for l in all_links:
        link_id = l.get("id")
        if link_id:
            key = str(link_id)
        else:
            key = (
                f"{l.get('link_type')}:{l.get('level')}:{l.get('from_line_no')}:"
                f"{l.get('from_document_id')}:{l.get('to_document_id')}:{l.get('to_line_no')}:{l.get('state')}"
            )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_links.append(l)

    for idx, l in enumerate(deduped_links):
        curr_prefix = f"{prefix}       " if idx == 0 else indent

        l_type = l.get("link_type", "")
        level = l.get("level", "header")
        from_line = l.get("from_line_no")
        target_no = l.get("target_doc_no") or l.get("to_doc_no") or "not recorded"
        target_type = (l.get("target_doc_type") or "PO").upper()
        target_line = l.get("to_line_no")
        state = l.get("state", "suggested")
        conf = l.get("confidence")
        conf_str = f"{conf:.2f}" if conf is not None else "—"
        method = l.get("method") or "content"

        # State color
        if state in ("confirmed", "auto"):
            state_disp = colorize(state, GREEN, no_color=no_color)
        elif state == "rejected":
            state_disp = colorize(state, RED, no_color=no_color)
        else:
            state_disp = colorize(state, YELLOW, no_color=no_color)

        if l_type == "grn_po":
            src_no = l.get("source_doc_no") or l.get("from_doc_no") or "GRN"
            if level == "line" and from_line is not None:
                to_line_str = f" line {target_line}" if target_line is not None else f" line {from_line}"
                link_desc = f"GRN {src_no} line {from_line} → PO {target_no}{to_line_str}"
            else:
                link_desc = f"GRN {src_no} header → PO {target_no}"
        else:
            if level == "line" and from_line is not None:
                to_line_str = f" line {target_line}" if target_line is not None else f" line {from_line}"
                link_desc = f"line {from_line} → {target_type} {target_no}{to_line_str}"
            else:
                link_desc = f"header → {target_type} {target_no}"

        out.append(f"{curr_prefix} {link_desc} · {state_disp} · {conf_str} · {method}")

    return out


def format_ledger_section(
    ledger_rows: Sequence[dict[str, Any]],
    no_color: bool = False,
) -> list[str]:
    """Format LEDGER section:

    LEDGER (current, not a run snapshot)   PO line 1 · ordered 1 · received 1 · billed 1 · open 0
    """
    out: list[str] = []
    title = colorize("LEDGER (current, not a run snapshot)", BOLD, no_color=no_color)

    if not ledger_rows:
        out.append(f"{title}   (not recorded / no linked PO ledger)")
        return out

    for idx, r in enumerate(ledger_rows):
        line_no = r.get("po_line_no", 1)
        ord_q = mask_qty(r.get("ordered", 0))
        rec_q = mask_qty(r.get("received_accepted", 0))
        bil_q = mask_qty(r.get("billed", 0))
        opn_q = mask_qty(r.get("open_qty", 0))

        prefix = f"{title}   " if idx == 0 else " " * 39
        out.append(f"{prefix}PO line {line_no} · ordered {ord_q} · received {rec_q} · billed {bil_q} · open {opn_q}")

    return out


def format_match_section(
    case_info: dict[str, Any] | None,
    line_results: Sequence[dict[str, Any]],
    mask: bool = False,
    no_color: bool = False,
) -> list[str]:
    """Format MATCH section:

    MATCH        run R-217 · Matched · reasons: none
                 line 1  PO 1 × 8,500 | GRN accepted 1 | INV 1 × 8,500   ✓
    """
    out: list[str] = []
    prefix = colorize("MATCH", BOLD, no_color=no_color)
    indent = " " * 12

    if not case_info:
        out.append(f"{prefix}        (not reconciled / no run recorded)")
        return out

    run_id = str(case_info.get("run_id", ""))[:8]
    status = (case_info.get("status") or "unlinked").lower()
    status_disp = {
        "matched": colorize("Matched", GREEN, no_color=no_color),
        "needs_review": colorize("Needs review", YELLOW, no_color=no_color),
        "unlinked": colorize("Unlinked", YELLOW, no_color=no_color),
        "duplicate": colorize("Duplicate", RED, no_color=no_color),
    }.get(status, status)

    reasons = case_info.get("reason_codes") or []
    reasons_str = ", ".join(reasons) if reasons else "none"

    out.append(f"{prefix}        run R-{run_id} · {status_disp} · reasons: {reasons_str}")

    for r in line_results:
        line_no = r.get("line_no", 1)
        po_q = mask_qty(r.get("po_qty"))
        po_r = mask_amount(r.get("po_rate"), mask=mask)
        grn_q = mask_qty(r.get("grn_accepted_cum"))
        inv_q = mask_qty(r.get("billed_qty"))
        inv_r = mask_amount(r.get("invoice_net_rate") or r.get("payable_rate"), mask=mask)

        line_st = (r.get("status") or "matched").lower()
        if line_st == "matched":
            m = mark_ok(no_color)
        elif line_st in ("needs_review", "pending"):
            m = mark_warn(no_color)
        else:
            m = mark_fail(no_color)

        line_reasons = r.get("reason_codes") or []
        reason_extra = f" [{', '.join(line_reasons)}]" if line_reasons else ""

        line_str = f"line {line_no}  PO {po_q} × {po_r} | GRN accepted {grn_q} | INV {inv_q} × {inv_r}   {m}{reason_extra}"
        out.append(f"{indent} {line_str}")

    return out


def format_run_mode(
    run_info: dict[str, Any] | None,
    cases: Sequence[dict[str, Any]],
    no_color: bool = False,
) -> list[str]:
    """Format output for `run` mode:

    RUN R-217 · succeeded · 10:43:02 · 4 s · Matched 2 · Needs review 2 · Unlinked 3
    INVOICE         STATUS        REASONS
    OSC/26-27/3877  Matched       —
    267             Needs review  TOTAL_MISMATCH, LOW_CONFIDENCE_DATA
    """
    out: list[str] = []
    if not run_info:
        out.append(colorize("RUN (no reconciliation runs recorded)", BOLD, no_color=no_color))
        return out

    run_id = str(run_info.get("id", ""))[:8]
    status = run_info.get("status", "unknown")
    start = run_info.get("started_at")
    finish = run_info.get("finished_at")

    time_str = format_time(start) if start else "not recorded"
    dur_str = "—"
    if start and finish:
        try:
            delta = (finish - start).total_seconds()
            dur_str = f"{round(delta)} s"
        except Exception:
            pass

    counts = run_info.get("status_counts") or {}
    matched_c = counts.get("matched", 0)
    review_c = counts.get("needs_review", 0)
    unlinked_c = counts.get("unlinked", 0)
    duplicate_c = counts.get("duplicate", 0)

    count_parts = [
        f"Matched {matched_c}",
        f"Needs review {review_c}",
        f"Unlinked {unlinked_c}",
    ]
    if duplicate_c:
        count_parts.append(f"Duplicate {duplicate_c}")

    header = f"RUN R-{run_id} · {status} · {time_str} · {dur_str} · " + " · ".join(count_parts)
    out.append(colorize(header, BOLD, no_color=no_color))

    table_hdr = f"{'INVOICE':<16} {'STATUS':<14} {'REASONS'}"
    out.append(colorize(table_hdr, BOLD, no_color=no_color))

    if not cases:
        out.append(f"{'—':<16} {'(0 cases)':<14} —")
        return out

    for c in cases:
        inv_no = str(c.get("doc_no") or c.get("invoice_id", ""))
        st = c.get("status", "unknown").replace("_", " ").title()
        if st.lower() == "matched":
            st_col = colorize(st, GREEN, no_color=no_color)
        elif st.lower() == "needs review":
            st_col = colorize(st, YELLOW, no_color=no_color)
        elif st.lower() == "duplicate":
            st_col = colorize(st, RED, no_color=no_color)
        else:
            st_col = st

        reasons = c.get("reason_codes") or []
        reasons_str = ", ".join(reasons) if reasons else "—"

        out.append(f"{inv_no:<16} {st_col:<23} {reasons_str}")

    return out


def format_queue_line(
    queue_counts: dict[str, tuple[int, int]], no_color: bool = False
) -> str:
    """Format QUEUES line:

    QUEUES  ingest 0/0 · parse 0/1 · mapping 313/1 · reconcile 0/0 · exports 0/0 · ai 1/0
    """
    title = colorize("QUEUES", BOLD, no_color=no_color)
    queues = ("ingest", "parse", "mapping", "reconcile", "exports", "ai")
    parts = []
    for q in queues:
        todo, doing = queue_counts.get(q, (0, 0))
        part = f"{q} {todo}/{doing}"
        if doing > 0:
            part = colorize(part, YELLOW, no_color=no_color)
        elif todo > 0:
            part = colorize(part, CYAN, no_color=no_color)
        parts.append(part)
    return f"{title}  " + " · ".join(parts)


def format_follow_event(
    event: dict[str, Any], mask: bool = False, no_color: bool = False
) -> str:
    """Format one activity line for follow mode."""
    time_str = format_time(event.get("time"))
    name = str(event.get("name", "")).ljust(14)
    kind = event.get("kind", "")
    detail = event.get("detail", "")

    if kind == "file_parsed":
        m = mark_ok(no_color)
        return f"{time_str} {name} {m} parsed · {detail}"
    if kind == "file_queued":
        m = mark_pending(no_color)
        return f"{time_str} {name} {m} queued · {detail}"
    if kind == "file_failed":
        m = mark_fail(no_color)
        return f"{time_str} {name} {m} failed · {detail}"
    if kind == "ai_queued":
        m = mark_pending(no_color)
        return f"{time_str} {name} AI {m} queued"
    if kind == "ai_done":
        m = mark_ok(no_color)
        return f"{time_str} {name} AI {m} {detail}"
    if kind == "ai_failed":
        m = mark_fail(no_color)
        return f"{time_str} {name} AI {m} {detail}"
    if kind == "link_suggested":
        m = mark_pending(no_color)
        return f"{time_str} {name} links {m} suggested → {detail}"
    if kind == "link_confirmed":
        m = mark_ok(no_color)
        return f"{time_str} {name} links {m} confirmed → {detail}"
    if kind == "run_done":
        m = mark_ok(no_color)
        return f"{time_str} {name} {m} {detail}"

    return f"{time_str} {name} · {detail}"
