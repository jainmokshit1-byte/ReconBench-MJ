"""Pure formatting and masking functions for ReconBench pipeline log watcher.

Strictly NO database access, NO network, NO file I/O.
Single-threaded, deterministic, safe against credential leakage.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

# Fixed timezone IST (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# ANSI Color Codes
ANSI_RESET = "\x1b[0m"
ANSI_CYAN = "\x1b[36m"
ANSI_GREEN = "\x1b[32m"
ANSI_YELLOW = "\x1b[33m"
ANSI_RED = "\x1b[31m"
ANSI_MAGENTA = "\x1b[35m"
ANSI_BLUE = "\x1b[34m"
ANSI_DIM = "\x1b[90m"

# 6 distinct colors for document tags (same document ID = same color)
DOC_TAG_COLORS = [
    ANSI_CYAN,
    ANSI_GREEN,
    ANSI_YELLOW,
    ANSI_BLUE,
    ANSI_MAGENTA,
    ANSI_RED,
]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text for accurate length calculation."""
    return _ANSI_RE.sub("", text)


def to_ist(ts: Any) -> datetime | None:
    """Convert timestamp or ISO string to IST datetime with fixed offset +05:30."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        dt = ts
    elif isinstance(ts, date):
        dt = datetime.combine(ts, datetime.min.time(), tzinfo=UTC)
    else:
        s = str(ts).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def format_time_ist(ts: Any) -> str:
    """Format event timestamp as HH:MM:SS in IST."""
    dt = to_ist(ts)
    if dt is None:
        return "00:00:00"
    return dt.strftime("%H:%M:%S")


def format_inr(value: Decimal | int | str, ascii_mode: bool = False) -> str:
    """Format a monetary amount in Indian numbering format from Decimal or int.

    Raises TypeError on float input to prevent precision loss.
    Example: 118000 -> ₹1,18,000.00 (or Rs 1,18,000.00 in ASCII mode).
    """
    if isinstance(value, float):
        raise TypeError("format_inr rejects float input; use Decimal, int, or string")
    if value is None:
        return "—"

    dec = Decimal(str(value))
    is_neg = dec < 0
    dec = abs(dec)

    sym = "Rs " if ascii_mode else "₹"
    dec_quant = dec.quantize(Decimal("0.01"))
    parts = f"{dec_quant:.2f}".split(".")
    int_str, frac_str = parts[0], parts[1]

    if len(int_str) <= 3:
        formatted_int = int_str
    else:
        last3 = int_str[-3:]
        remaining = int_str[:-3]
        chunks: list[str] = []
        while remaining:
            chunks.append(remaining[-2:])
            remaining = remaining[:-2]
        chunks.reverse()
        formatted_int = ",".join(chunks) + "," + last3

    res = f"{sym}{formatted_int}.{frac_str}"
    return f"-{res}" if is_neg else res


def mask_gstin(gstin: str | None, ascii_mode: bool = False) -> str:
    """Format GSTIN as …<last 4> in both masked and show modes."""
    if not gstin or not str(gstin).strip():
        return "—"
    clean = str(gstin).strip()
    prefix = "..." if ascii_mode else "…"
    if len(clean) <= 4:
        return f"{prefix}{clean}"
    return f"{prefix}{clean[-4:]}"


def mask_vendor(name: str | None, vendor_id: Any = None, mode: str = "masked") -> str:
    """Format vendor name according to mode.

    masked: V-<last 4 hex chars of vendor id>
    show: vendor name (max 30 chars)
    """
    if mode == "show":
        return str(name)[:30] if name else "Unassigned"
    # masked mode
    if vendor_id is not None:
        clean_id = re.sub(r"[^0-9a-fA-F]", "", str(vendor_id))
        last4 = clean_id[-4:].lower() if len(clean_id) >= 4 else (clean_id.lower() or "0000")
        return f"V-{last4}"
    return "Unassigned"


def mask_file_name(display_name: str | None, file_id: Any = None, mode: str = "masked") -> str:
    """Format file name according to mode.

    masked: <extension> #<last 4 of file id>
    show: display name
    """
    if mode == "show":
        return display_name or "file"
    # masked mode
    ext = "file"
    if display_name and "." in display_name:
        ext = "." + display_name.rsplit(".", 1)[1]
    clean_id = re.sub(r"[^0-9a-fA-F]", "", str(file_id or ""))
    last4 = clean_id[-4:].lower() if len(clean_id) >= 4 else (clean_id.lower() or "0000")
    return f"{ext} #{last4}"


def mask_amount(value: Any, mode: str = "masked", ascii_mode: bool = False) -> str:
    """Format amount: masked gives ₹*** / Rs***; show gives formatted Indian currency."""
    if mode == "show":
        return format_inr(value, ascii_mode=ascii_mode)
    return "Rs***" if ascii_mode else "₹***"


def format_doc_tag(
    doc_type: str | None,
    identifier: str | None,
    doc_id: Any = None,
    mode: str = "masked",
) -> str:
    """Format document tag: <INV|PO|GRN|FILE> <doc no or file name> #<last 4 hex>."""
    dtype = (doc_type or "").upper()
    if dtype in ("IMPORT", "BATCH", "QUEUE", "HEALTH", "EXPORT"):
        return dtype

    clean_id = re.sub(r"[^0-9a-fA-F]", "", str(doc_id or ""))
    last4 = clean_id[-4:].lower() if len(clean_id) >= 4 else (clean_id.lower() or "0000")

    if dtype == "FILE":
        if mode == "masked":
            fname = mask_file_name(identifier, file_id=doc_id, mode=mode)
            return f"FILE {fname}"
        fname = identifier or "file"
        return f"FILE {fname} #{last4}"

    type_tag = (
        "INV"
        if dtype in ("INVOICE", "INV")
        else ("PO" if dtype == "PO" else ("GRN" if dtype == "GRN" else dtype or "DOC"))
    )
    doc_no = identifier or "unknown"
    return f"{type_tag} {doc_no} #{last4}"


def get_doc_tag_color(doc_id: Any, no_color: bool = False) -> str:
    """Pick one of 6 consistent colors for the document based on its UUID."""
    if no_color or not doc_id:
        return ""
    clean_id = re.sub(r"[^0-9a-fA-F]", "", str(doc_id))
    if not clean_id:
        return ""
    try:
        val = int(clean_id[-4:], 16)
        return DOC_TAG_COLORS[val % len(DOC_TAG_COLORS)]
    except Exception:
        return ""


def format_status_mark(symbol: str, ascii_mode: bool = False, no_color: bool = False) -> str:
    """Format status symbol: ✓ green, ⚠ yellow, ✗ red."""
    if ascii_mode:
        if symbol == "✓":
            return "OK" if no_color else f"{ANSI_GREEN}OK{ANSI_RESET}"
        if symbol == "⚠":
            return "!" if no_color else f"{ANSI_YELLOW}!{ANSI_RESET}"
        if symbol == "✗":
            return "X" if no_color else f"{ANSI_RED}X{ANSI_RESET}"
        return symbol

    if symbol == "✓":
        return symbol if no_color else f"{ANSI_GREEN}{symbol}{ANSI_RESET}"
    if symbol == "⚠":
        return symbol if no_color else f"{ANSI_YELLOW}{symbol}{ANSI_RESET}"
    if symbol == "✗":
        return symbol if no_color else f"{ANSI_RED}{symbol}{ANSI_RESET}"
    return symbol


def format_status_text(status: str, no_color: bool = False) -> str:
    """Format case status text with appropriate ANSI color."""
    clean = status.strip()
    norm = clean.lower()
    color = ""
    if "matched" in norm:
        color = ANSI_GREEN
    elif "needs review" in norm or "review" in norm:
        color = ANSI_YELLOW
    elif "unlinked" in norm or "exception" in norm:
        color = ANSI_RED
    elif "duplicate" in norm:
        color = ANSI_MAGENTA
    elif "pending" in norm:
        color = ANSI_BLUE

    if no_color or not color:
        return clean
    return f"{color}{clean}{ANSI_RESET}"


def render_event_line(
    timestamp: Any,
    icon: str,
    stage: str,
    doc_tag: str,
    facts: list[str],
    doc_id: Any = None,
    ascii_mode: bool = False,
    no_color: bool = False,
) -> str:
    """Render a single pipeline watcher line adhering to exact section 7 specifications.

    Format:
      HH:MM:SS <icon> <STAGE padded to 7> <doc tag padded/truncated to 30> │ <facts joined by " · ">
    Line length <= 160 characters.
    """
    time_str = format_time_ist(timestamp)

    # Icon handling (ASCII mode omits icons)
    icon_str = "" if ascii_mode else (f"{icon} " if icon else "")

    # Stage padded to 7 chars
    stage_str = stage[:7].ljust(7)
    colored_stage = f"{ANSI_CYAN}{stage_str}{ANSI_RESET}" if not no_color else stage_str

    # Doc tag padded/truncated to 30 chars
    tag_clean = doc_tag[:30].ljust(30)
    tag_color = get_doc_tag_color(doc_id, no_color=no_color)
    colored_tag = f"{tag_color}{tag_clean}{ANSI_RESET}" if tag_color else tag_clean

    # Separator
    sep = "|" if ascii_mode else "│"

    # Assemble prefix
    prefix_colored = f"{time_str} {icon_str}{colored_stage} {colored_tag} {sep} "
    prefix_plain = f"{time_str} {icon_str}{stage_str} {tag_clean} {sep} "

    # Facts joined by " · "
    facts_joined = " · ".join(f for f in facts if f)

    # 160 character limit enforcement
    max_fact_len = 160 - len(prefix_plain)
    ellipsis = "..." if ascii_mode else "…"
    if len(strip_ansi(facts_joined)) > max_fact_len:
        allowed = max(0, max_fact_len - len(ellipsis))
        # Cut facts at allowed length
        cut_facts = facts_joined[:allowed] + ellipsis
        return f"{prefix_colored}{cut_facts}"

    return f"{prefix_colored}{facts_joined}"


def render_date_separator(dt: date | datetime, ascii_mode: bool = False) -> str:
    """Format date separator line in --once mode: ── dd/mm/yyyy ──."""
    d_obj = dt if isinstance(dt, date) else to_ist(dt).date()  # type: ignore
    d_str = d_obj.strftime("%d/%m/%Y")
    if ascii_mode:
        return f"-- {d_str} --"
    return f"── {d_str} ──"


def render_startup_banner(
    values_mode: str,
    interval_s: int,
    since_ist: str,
    page_reader: str,
    doc_count: int,
    queue_status: str,
    ascii_mode: bool = False,
    no_color: bool = False,
) -> str:
    """Format single startup banner line."""
    banner = (
        f"ReconBench pipeline log · values={values_mode} · every {interval_s}s · "
        f"since {since_ist} · page reader: {page_reader} · "
        f"documents: {doc_count} · queue: {queue_status}"
    )
    if no_color:
        return banner
    return f"{ANSI_CYAN}{banner}{ANSI_RESET}"


def render_heartbeat(
    last_event_time: str,
    queue_status: str = "queue idle",
    ascii_mode: bool = False,
    no_color: bool = False,
) -> str:
    """Format 10-minute idle heartbeat line."""
    dot = "." if ascii_mode else "·"
    text = f"{dot} watching {dot} {queue_status} {dot} last event {last_event_time}"
    if no_color:
        return text
    return f"{ANSI_DIM}{text}{ANSI_RESET}"
