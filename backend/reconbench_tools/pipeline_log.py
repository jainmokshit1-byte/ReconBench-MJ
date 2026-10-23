"""ReconBench Pipeline Log Watcher (WI-TRC2).

Strictly READ-ONLY pipeline watcher container entrypoint.
Monitors document ingestion, OCR, AI readers, linking, reconciliation,
queues, and user actions. Emits clean, coloured log lines to stdout.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from reconbench_tools.pipeline_log_queries import (
    get_banner_metadata,
    get_db_now,
    query_ai_health_events,
    query_ai_requests,
    query_ai_suggestions_details,
    query_doc_events,
    query_export_events,
    query_failed_jobs,
    query_field_evidence_summary,
    query_file_events,
    query_link_events,
    query_link_resolutions,
    query_recon_runs_and_cases,
    query_user_actions,
    query_vendor_resolutions,
)
from reconbench_tools.pipeline_log_render import (
    format_doc_tag,
    format_status_mark,
    format_status_text,
    format_time_ist,
    mask_amount,
    mask_gstin,
    mask_vendor,
    render_date_separator,
    render_event_line,
    render_heartbeat,
    render_startup_banner,
    to_ist,
)
from reconbench_tools.trace import create_trace_engine
from reconbench_tools.trace_queries import get_queue_counts, resolve_workspace

# Global flag for clean SIGTERM handling
_RUNNING = True


def _handle_sigterm(_signum: int, _frame: Any) -> None:
    global _RUNNING
    _RUNNING = False


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def parse_duration(s: str) -> timedelta:
    """Parse duration string like 2h, 30m, 1d, 120s."""
    clean = s.strip().lower()
    m = re.match(r"^(\d+)\s*([smhd]?)$", clean)
    if not m:
        raise ValueError(f"invalid duration format: {s}")
    val, unit = int(m.group(1)), m.group(2)
    if unit in ("s", ""):
        return timedelta(seconds=val)
    if unit == "m":
        return timedelta(minutes=val)
    if unit == "h":
        return timedelta(hours=val)
    if unit == "d":
        return timedelta(days=val)
    return timedelta(hours=val)


class PipelineWatcher:
    """Stateful watcher for ReconBench pipeline events."""

    def __init__(
        self,
        engine: Engine | None = None,
        values_mode: str = "masked",
        interval_s: int = 3,
        ascii_mode: bool = False,
        no_color: bool = False,
    ) -> None:
        self.engine = engine
        self.values_mode = values_mode
        self.base_interval_s = max(2, interval_s)
        self.interval_s = self.base_interval_s
        self.ascii_mode = ascii_mode
        self.no_color = no_color

        # Bounded history (5000 items)
        self.seen_keys: set[tuple[str, Any]] = set()
        self.seen_order: deque[tuple[str, Any]] = deque(maxlen=5000)

        # Mutable entity state tracking: (table, id) -> status_str
        self.last_status: dict[tuple[str, Any], str] = {}

        # Queue tracking
        self.last_queue_counts: dict[str, tuple[int, int]] | None = None
        self.last_queue_print_time = 0.0

        # Heartbeat tracking
        self.last_print_time = time.time()
        self.last_event_time_str = "—"

        # Date tracking for --once separator
        self.last_printed_date: str | None = None

        self.workspace_id: UUID | None = None
        self.last_watermark: datetime | None = None

    def mark_seen(self, table: str, entity_id: Any) -> bool:
        """Record (table, id) key. Returns True if newly seen, False if already seen."""
        key = (table, str(entity_id))
        if key in self.seen_keys:
            return False
        if len(self.seen_order) >= self.seen_order.maxlen:  # type: ignore[operator]
            old = self.seen_order.popleft()
            self.seen_keys.discard(old)
        self.seen_keys.add(key)
        self.seen_order.append(key)
        return True

    def check_status_changed(self, table: str, entity_id: Any, current_status: str) -> bool:
        """Return True if row status changed compared to last known status."""
        key = (table, str(entity_id))
        prev = self.last_status.get(key)
        if prev != current_status:
            self.last_status[key] = current_status
            return True
        return False

    def print_line(self, line: str, event_time: datetime | None = None) -> None:
        """Print line with date separator if date changed and flush immediately."""
        if event_time is not None:
            ist_dt = to_ist(event_time)
            if ist_dt:
                d_str = ist_dt.strftime("%d/%m/%Y")
                if self.last_printed_date != d_str:
                    if self.last_printed_date is not None:
                        sep = render_date_separator(ist_dt.date(), ascii_mode=self.ascii_mode)
                        print(sep, flush=True)
                    self.last_printed_date = d_str
                self.last_event_time_str = ist_dt.strftime("%H:%M:%S")

        print(line, flush=True)
        self.last_print_time = time.time()

    def process_cycle(self, conn: Connection, since: datetime) -> list[str]:  # noqa: C901
        """Run all watcher queries for one cycle, assemble lines, and apply flood control."""
        if not self.workspace_id:
            self.workspace_id, _ = resolve_workspace(conn)
        wid = self.workspace_id

        output_lines: list[tuple[datetime, str]] = []

        # 1. Source files
        file_events = query_file_events(conn, wid, since)
        for f in file_events:
            fid = f["id"]
            st = str(f["status"] or "unknown")
            is_new = self.mark_seen("source_file", fid)
            status_changed = self.check_status_changed("source_file", fid, st)
            if is_new or status_changed:
                t = f.get("parse_finished_at") or f.get("uploaded_at") or since
                tag = format_doc_tag(
                    "FILE", f.get("display_name"), doc_id=fid, mode=self.values_mode
                )
                zone_fmt = f"{f.get('zone', 'file')}/{f.get('format', '')}"
                size_kb = f"{round(int(f.get('size_bytes') or 0) / 1024, 1)} KB"
                pages = f"{f.get('page_count') or 1}p"

                status_desc = st
                if st == "queued":
                    status_desc = "Queued"
                elif st == "parsing":
                    status_desc = "Parsing"
                elif st == "parsed":
                    docs = f.get("docs_found") or f.get("docs_new") or 0
                    status_desc = f"Parsed {docs} doc{'s' if docs != 1 else ''}"
                elif st == "failed":
                    err = f.get("error_code") or ""
                    status_desc = f"Failed {err}".strip()

                line = render_event_line(
                    timestamp=t,
                    icon="📄",
                    stage="FILE",
                    doc_tag=tag,
                    facts=[zone_fmt, size_kb, pages, status_desc],
                    doc_id=fid,
                    ascii_mode=self.ascii_mode,
                    no_color=self.no_color,
                )
                output_lines.append((t, line))

        # 2. Documents & Versions
        doc_events = query_doc_events(conn, wid, since)
        new_versions = [
            d for d in doc_events if self.mark_seen("document_version", d["version_id"])
        ]

        # Fetch field evidence for new invoice versions
        inv_versions = [d["version_id"] for d in new_versions if d.get("doc_type") == "invoice"]
        fe_summary = query_field_evidence_summary(conn, wid, inv_versions) if inv_versions else {}

        # Flood control buckets for PO/GRN documents
        po_doc_events: list[dict[str, Any]] = []
        grn_doc_events: list[dict[str, Any]] = []

        for d in new_versions:
            dtype = str(d.get("doc_type") or "").lower()
            if dtype == "po":
                po_doc_events.append(d)
                continue
            if dtype == "grn":
                grn_doc_events.append(d)
                continue

            # Invoice document event
            t = d["created_at"]
            tag = format_doc_tag(
                d.get("doc_type"), d.get("doc_no"), doc_id=d["document_id"], mode=self.values_mode
            )
            ex_method = str(d.get("extraction_method") or "")
            if d.get("derived_from_version_id") or ex_method == "manual":
                how_read = "person correction"
            elif ex_method == "pdf_text":
                how_read = "digital PDF · OCR not needed"
            elif ex_method == "pdf_ocr":
                how_read = "scanned PDF · OCR ran"
            elif ex_method in ("xml_profile", "csv_mapping"):
                how_read = "Tally/structured import · no OCR"
            else:
                how_read = ex_method

            amt_str = mask_amount(
                d.get("grand_total"), mode=self.values_mode, ascii_mode=self.ascii_mode
            )
            total_str = f"total {amt_str}"
            facts = [how_read, f"v{d.get('seq', 1)}", total_str]
            if d.get("needs_verification"):
                reasons = ", ".join(d.get("verification_reasons") or ["LOW_CONFIDENCE"])
                warn_mark = format_status_mark(
                    "⚠", ascii_mode=self.ascii_mode, no_color=self.no_color
                )
                facts.append(f"needs review {warn_mark}: {reasons}")

            line = render_event_line(
                timestamp=t,
                icon="🧾",
                stage="DOC",
                doc_tag=tag,
                facts=facts,
                doc_id=d["document_id"],
                ascii_mode=self.ascii_mode,
                no_color=self.no_color,
            )
            output_lines.append((t, line))

            # 3. FIELDS event for invoices
            vid = d["version_id"]
            if vid in fe_summary:
                f_info = fe_summary[vid]
                f_facts = [
                    f"rules/OCR: {f_info['rule_count']}",
                    f"AI: {f_info['ai_count']}",
                    f"person: {f_info['manual_count']}",
                ]
                low_conf = f_info.get("low_conf") or []
                if low_conf:
                    low_desc = ", ".join(f"{name} ({conf:.2f})" for name, conf in low_conf[:3])
                    f_facts.append(f"low conf: {low_desc}")

                fe_line = render_event_line(
                    timestamp=t,
                    icon="🔍",
                    stage="FIELDS",
                    doc_tag=tag,
                    facts=f_facts,
                    doc_id=d["document_id"],
                    ascii_mode=self.ascii_mode,
                    no_color=self.no_color,
                )
                output_lines.append((t, fe_line))

        # Handle PO/GRN documents with flood control (<= 5 individually, else summary)
        if len(po_doc_events) + len(grn_doc_events) <= 5:
            for p in po_doc_events + grn_doc_events:
                t = p["created_at"]
                tag = format_doc_tag(
                    p.get("doc_type"),
                    p.get("doc_no"),
                    doc_id=p["document_id"],
                    mode=self.values_mode,
                )
                line = render_event_line(
                    timestamp=t,
                    icon="🧾",
                    stage="DOC",
                    doc_tag=tag,
                    facts=["Tally/structured import · no OCR", f"v{p.get('seq', 1)}"],
                    doc_id=p["document_id"],
                    ascii_mode=self.ascii_mode,
                    no_color=self.no_color,
                )
                output_lines.append((t, line))
        else:
            # Summary line for import
            sum_time = (
                po_doc_events[0]["created_at"]
                if po_doc_events
                else grn_doc_events[0]["created_at"]
            )
            sum_facts = []
            if po_doc_events:
                sum_facts.append(f"{len(po_doc_events)} PO")
            if grn_doc_events:
                sum_facts.append(f"{len(grn_doc_events)} GRN")
            summary_text = f"Tally import: {' · '.join(sum_facts)} documents"
            line = render_event_line(
                timestamp=sum_time,
                icon="🧾",
                stage="DOC",
                doc_tag="IMPORT",
                facts=[summary_text],
                doc_id=None,
                ascii_mode=self.ascii_mode,
                no_color=self.no_color,
            )
            output_lines.append((sum_time, line))

        # 4. Vendor resolutions
        vendors = query_vendor_resolutions(conn, wid, since)
        for v in vendors:
            did = v["document_id"]
            if self.mark_seen("vendor_resolved", did):
                t = v["vendor_resolved_at"] or since
                tag = format_doc_tag(
                    v.get("doc_type"), v.get("doc_no"), doc_id=did, mode=self.values_mode
                )
                rule = str(v.get("vendor_rule") or "Unassigned")
                if rule in ("name_exact", "fuzzy"):
                    rule_desc = "name"
                elif rule == "gstin":
                    rule_desc = "GSTIN"
                elif rule == "pan":
                    rule_desc = "PAN"
                elif rule == "alias":
                    rule_desc = "alias"
                elif rule == "manual":
                    rule_desc = "person"
                else:
                    rule_desc = "Unassigned"

                v_rep = mask_vendor(
                    v.get("vendor_name"), vendor_id=v.get("vendor_id"), mode=self.values_mode
                )
                gstin_rep = mask_gstin(v.get("vendor_gstin"), ascii_mode=self.ascii_mode)

                line = render_event_line(
                    timestamp=t,
                    icon="🏷",
                    stage="VENDOR",
                    doc_tag=tag,
                    facts=[f"resolved: {rule_desc}", v_rep, f"GSTIN {gstin_rep}"],
                    doc_id=did,
                    ascii_mode=self.ascii_mode,
                    no_color=self.no_color,
                )
                output_lines.append((t, line))

        # 5. AI requests and suggestions
        ai_reqs = query_ai_requests(conn, wid, since)
        cache_ids = [r["ai_cache_id"] for r in ai_reqs if r.get("ai_cache_id")]
        vids = [r["subject_id"] for r in ai_reqs if r.get("subject_id")]
        caches, warnings = (
            query_ai_suggestions_details(conn, wid, cache_ids, vids)
            if (cache_ids or vids)
            else ({}, {})
        )

        for r in ai_reqs:
            rid = r["request_id"]
            st = str(r["request_state"] or "queued")
            is_new = self.mark_seen("ai_request", rid)
            status_changed = self.check_status_changed("ai_request", rid, st)

            if is_new or status_changed:
                t = r.get("finished_at") or r.get("created_at") or since
                tag = format_doc_tag(
                    r.get("doc_type"),
                    r.get("doc_no"),
                    doc_id=r.get("document_id"),
                    mode=self.values_mode,
                )
                hook = str(r.get("hook") or "vision")
                prov_model = f"{r.get('provider', '')} {r.get('model', '')}".strip()

                outcome = str(r.get("apply_outcome") or r.get("call_outcome") or st)
                if r.get("error_code"):
                    outcome = f"failed {r['error_code']}"

                latency_s = f"{round((r.get('latency_ms') or 0) / 1000, 1)}s"
                tokens = (r.get("tokens_input") or r.get("tokens_in") or 0) + (
                    r.get("tokens_output") or r.get("tokens_out") or 0
                )
                tokens_str = f"{tokens:,} tokens" if tokens else ""

                r_facts = [hook, prov_model, outcome, latency_s]
                if tokens_str:
                    r_facts.append(tokens_str)
                if r.get("call_outcome") == "cache_hit":
                    r_facts.append("cache hit")

                # Line 1: AI request result
                line1 = render_event_line(
                    timestamp=t,
                    icon="🤖",
                    stage="AI",
                    doc_tag=tag,
                    facts=r_facts,
                    doc_id=r.get("document_id"),
                    ascii_mode=self.ascii_mode,
                    no_color=self.no_color,
                )
                output_lines.append((t, line1))

                # Line 2: AI suggestions
                cid = r.get("ai_cache_id")
                if cid and cid in caches:
                    sugg_fields = caches[cid]
                    sugg_count = len(sugg_fields)

                    diff_facts: list[str] = [f"{sugg_count} suggestions"]
                    if self.values_mode == "show":
                        allowed_show = {
                            "document_number", "doc_no", "invoice_no",
                            "document_date", "doc_date", "date",
                            "po_reference", "po_refs", "grn_reference", "grn_refs",
                            "taxable_total", "grand_total", "total",
                        }
                        diffs = []
                        for sf in sugg_fields:
                            fname = str(sf.get("name") or "")
                            if fname.lower() in allowed_show:
                                fval = sf.get("text")
                                if fval is not None and str(fval).strip():
                                    diffs.append(f"{fname} -> AI '{fval}'")
                            if len(diffs) >= 3:
                                break
                        if diffs:
                            diff_facts.append(" · ".join(diffs))
                    else:
                        names = [str(sf.get("name")) for sf in sugg_fields if sf.get("name")]
                        if names:
                            diff_facts.append(", ".join(names[:4]))

                    # Rejection marks
                    sub_vid = r.get("subject_id")
                    if sub_vid in warnings and warnings[sub_vid]:
                        w_counts = len(warnings[sub_vid])
                        diff_facts.append(f"{w_counts} check_failed")

                    line2 = render_event_line(
                        timestamp=t,
                        icon="🤖",
                        stage="AI",
                        doc_tag=tag,
                        facts=diff_facts,
                        doc_id=r.get("document_id"),
                        ascii_mode=self.ascii_mode,
                        no_color=self.no_color,
                    )
                    output_lines.append((t, line2))

        # 6. Links and link resolutions
        links = query_link_events(conn, wid, since)
        new_links = [lk for lk in links if self.mark_seen("link", lk["link_id"])]

        # Group new links per invoice
        links_by_inv: dict[UUID, list[dict[str, Any]]] = {}
        for lk in new_links:
            inv_id = lk["from_document_id"]
            if inv_id not in links_by_inv:
                links_by_inv[inv_id] = []
            links_by_inv[inv_id].append(lk)

        for inv_id, lk_list in links_by_inv.items():
            sample = lk_list[0]
            t = sample["created_at"]
            tag = format_doc_tag(
                sample.get("from_doc_type"),
                sample.get("from_doc_no"),
                doc_id=inv_id,
                mode=self.values_mode,
            )
            target_no = sample.get("to_doc_no") or "PO"
            level = sample.get("level") or "header"
            state = sample.get("state") or "auto"
            state_colored = format_status_text(state, no_color=self.no_color)
            method = sample.get("method") or "labeled"

            facts = [f"PO {target_no}", str(level), state_colored, str(method)]
            if level == "line":
                facts.append(f"{len(lk_list)} line{'s' if len(lk_list) != 1 else ''} linked")

            line = render_event_line(
                timestamp=t,
                icon="🔗",
                stage="LINK",
                doc_tag=tag,
                facts=facts,
                doc_id=inv_id,
                ascii_mode=self.ascii_mode,
                no_color=self.no_color,
            )
            output_lines.append((t, line))

        # Link resolutions (when nothing linked)
        resolutions = query_link_resolutions(conn, wid, since)
        for res in resolutions:
            did = res["document_id"]
            if self.mark_seen("link_resolution", res["id"]):
                t = res["computed_at"]
                if res.get("outcome") == "unlinked":
                    tag = format_doc_tag(
                        res.get("doc_type"), res.get("doc_no"), doc_id=did, mode=self.values_mode
                    )
                    code = res.get("detail_code") or "NO_MATCH"
                    candidates = res.get("candidates") or []
                    cand_desc = ""
                    if candidates and isinstance(candidates, list):
                        cand_scores = [
                            f"{c.get('doc_no', 'cand')} ({float(c.get('score', 0)):.2f})"
                            for c in candidates[:2]
                        ]
                        cand_desc = f"candidates: {', '.join(cand_scores)}"

                    facts = (
                        [f"unlinked: {code}", cand_desc] if cand_desc else [f"unlinked: {code}"]
                    )
                    line = render_event_line(
                        timestamp=t,
                        icon="🔗",
                        stage="LINK",
                        doc_tag=tag,
                        facts=facts,
                        doc_id=did,
                        ascii_mode=self.ascii_mode,
                        no_color=self.no_color,
                    )
                    output_lines.append((t, line))

        # 7. User / Person actions
        user_actions = query_user_actions(conn, wid, since)
        for ua in user_actions:
            if self.mark_seen("user_action", ua["id"]):
                t = ua["time"]
                tag = format_doc_tag(
                    ua.get("doc_type"),
                    ua.get("doc_no"),
                    doc_id=ua.get("doc_id"),
                    mode=self.values_mode,
                )
                u_name = ua.get("user_name") or "person"
                action = ua.get("action") or "action"

                if action == "document.correct":
                    ast = ua.get("after_state") or {}
                    if ast.get("ai_suggestion_refs"):
                        v_sh = str(ast.get("document_version_id", ""))[:4]
                        act_desc = f"{u_name} accepted AI suggestion (v{v_sh})"
                    else:
                        act_desc = f"{u_name} corrected document"
                elif action == "confirm_link":
                    act_desc = f"{u_name} confirmed link"
                else:
                    act_desc = f"{u_name} {action}"
                    if ua.get("reason"):
                        act_desc += f" ({ua['reason']})"

                line = render_event_line(
                    timestamp=t,
                    icon="👤",
                    stage="USER",
                    doc_tag=tag,
                    facts=[act_desc],
                    doc_id=ua.get("doc_id"),
                    ascii_mode=self.ascii_mode,
                    no_color=self.no_color,
                )
                output_lines.append((t, line))

        # 8. Reconciliation runs and cases
        runs, cases = query_recon_runs_and_cases(conn, wid, since)
        for r in runs:
            rid = r["id"]
            st = str(r["status"] or "queued")
            is_new = self.mark_seen("recon_run", rid)
            st_changed = self.check_status_changed("recon_run", rid, st)

            if is_new and st in ("queued", "running"):
                t = r.get("started_at") or r.get("queued_at") or since
                recon_facts = [
                    f"run started: {r.get('total_cases', 0)} cases",
                    f"trigger: {r.get('trigger_kind', 'auto')}",
                ]
                line = render_event_line(
                    timestamp=t,
                    icon="⚖",
                    stage="RECON",
                    doc_tag="BATCH",
                    facts=recon_facts,
                    doc_id=None,
                    ascii_mode=self.ascii_mode,
                    no_color=self.no_color,
                )
                output_lines.append((t, line))
            elif st_changed and st in ("succeeded", "failed"):
                t = r.get("finished_at") or since
                counts = r.get("status_counts") or {}
                cnt_str = (
                    f"matched {counts.get('matched', 0)} · "
                    f"needs review {counts.get('needs_review', 0)}"
                )
                dur_s = ""
                if r.get("started_at") and r.get("finished_at"):
                    delta = (r["finished_at"] - r["started_at"]).total_seconds()
                    dur_s = f"{delta:.1f}s"
                facts = [f"run finished: {st}", cnt_str]
                if dur_s:
                    facts.append(dur_s)

                line = render_event_line(
                    timestamp=t,
                    icon="⚖",
                    stage="RECON",
                    doc_tag="BATCH",
                    facts=facts,
                    doc_id=None,
                    ascii_mode=self.ascii_mode,
                    no_color=self.no_color,
                )
                output_lines.append((t, line))

        # Individual invoice recon cases (max 30 per run)
        new_cases = [c for c in cases if self.mark_seen("recon_case", c["id"])]
        printed_cases = new_cases[:30]
        for c in printed_cases:
            t = c["created_at"]
            tag = format_doc_tag(
                "INV", c.get("doc_no"), doc_id=c["invoice_id"], mode=self.values_mode
            )
            c_status = str(c.get("status") or "pending")
            status_colored = format_status_text(c_status, no_color=self.no_color)
            if "matched" in c_status.lower():
                ch_mark = format_status_mark(
                    "✓", ascii_mode=self.ascii_mode, no_color=self.no_color
                )
                status_colored += f" {ch_mark}"

            inv_tot = mask_amount(
                c.get("invoice_total"), mode=self.values_mode, ascii_mode=self.ascii_mode
            )
            exp_tot = mask_amount(
                c.get("expected_taxable"), mode=self.values_mode, ascii_mode=self.ascii_mode
            )
            var_tot = mask_amount(
                c.get("variance"), mode=self.values_mode, ascii_mode=self.ascii_mode
            )

            c_facts = [
                status_colored,
                f"invoice {inv_tot}",
                f"expected {exp_tot}",
                f"variance {var_tot}",
            ]
            if c.get("reason_codes"):
                rcs = ", ".join(c["reason_codes"])
                c_facts.append(f"codes: {rcs}")

            line = render_event_line(
                timestamp=t,
                icon="⚖",
                stage="RECON",
                doc_tag=tag,
                facts=c_facts,
                doc_id=c["invoice_id"],
                ascii_mode=self.ascii_mode,
                no_color=self.no_color,
            )
            output_lines.append((t, line))

        if len(new_cases) > 30:
            overflow = len(new_cases) - 30
            t = new_cases[30]["created_at"]
            line = render_event_line(
                timestamp=t,
                icon="⚖",
                stage="RECON",
                doc_tag="BATCH",
                facts=[f"… +{overflow} more cases"],
                doc_id=None,
                ascii_mode=self.ascii_mode,
                no_color=self.no_color,
            )
            output_lines.append((t, line))

        # 9. Exports
        exports = query_export_events(conn, wid, since)
        for exp in exports:
            eid = exp["id"]
            st = str(exp["status"] or "queued")
            is_new = self.mark_seen("export_file", eid)
            st_changed = self.check_status_changed("export_file", eid, st)
            if is_new or st_changed:
                t = exp.get("built_at") or exp.get("created_at") or since
                if st == "ready":
                    facts = ["Export ready", f"{exp.get('case_count', 0)} rows"]
                elif st == "failed":
                    err = exp.get("error_code") or "error"
                    facts = ["Export failed", str(err)]
                else:
                    facts = [f"Export {st}"]

                line = render_event_line(
                    timestamp=t,
                    icon="📤",
                    stage="EXPORT",
                    doc_tag="EXPORT",
                    facts=facts,
                    doc_id=None,
                    ascii_mode=self.ascii_mode,
                    no_color=self.no_color,
                )
                output_lines.append((t, line))

        # 10. AI Health
        healths = query_ai_health_events(conn, wid, since)
        for h in healths:
            hid = h["id"]
            prov = str(h.get("provider") or "ai")
            has_err = bool(
                h.get("last_error_code")
                and (
                    not h.get("connected_at")
                    or (
                        h.get("last_error_at")
                        and h.get("connected_at")
                        and h["last_error_at"] >= h["connected_at"]
                    )
                )
            )
            err_code = str(h.get("last_error_code") or "error") if has_err else ""
            is_conn = (
                bool(h.get("connected_at") or h.get("last_health_ok") or h.get("last_test_ok"))
                and not has_err
            )

            state_desc = (
                f"Error: {err_code}" if has_err else ("Connected" if is_conn else "Disconnected")
            )
            st_key = f"{prov}:{state_desc}"

            is_new = self.mark_seen("ai_health", hid)
            st_changed = self.check_status_changed("ai_health", hid, st_key)
            if is_new or st_changed:
                t = (
                    h.get("last_error_at")
                    or h.get("last_health_at")
                    or h.get("last_test_at")
                    or h.get("connected_at")
                    or h.get("updated_at")
                    or since
                )
                h_facts = [f"{prov} {state_desc}"]
                last_check = h.get("last_health_at") or h.get("last_test_at")
                if last_check:
                    h_facts.append(f"last check {format_time_ist(last_check)}")

                line = render_event_line(
                    timestamp=t,
                    icon="⚙",
                    stage="HEALTH",
                    doc_tag="HEALTH",
                    facts=h_facts,
                    doc_id=None,
                    ascii_mode=self.ascii_mode,
                    no_color=self.no_color,
                )
                output_lines.append((t, line))

        # 11. Queue counts & failed jobs (printed only when numbers change and >= 10s apart)
        now_sec = time.time()
        q_counts = get_queue_counts(conn)
        all_zero = all(todo == 0 and doing == 0 for todo, doing in q_counts.values())

        if q_counts != self.last_queue_counts and (now_sec - self.last_queue_print_time >= 10.0):
            self.last_queue_counts = q_counts
            self.last_queue_print_time = now_sec

            if all_zero:
                q_facts = ["queue idle"]
            else:
                active_parts = [
                    f"{q}: {todo} todo, {doing} doing"
                    for q, (todo, doing) in sorted(q_counts.items())
                    if todo > 0 or doing > 0
                ]
                q_facts = [" · ".join(active_parts)]

            # Check for failed jobs
            failed = query_failed_jobs(conn, limit=3)
            for fj in failed:
                q_facts.append(f"failed: {fj.get('task_name')} ({fj.get('status')})")

            q_time = get_db_now(conn)
            line = render_event_line(
                timestamp=q_time,
                icon="📚",
                stage="QUEUE",
                doc_tag="QUEUE",
                facts=q_facts,
                doc_id=None,
                ascii_mode=self.ascii_mode,
                no_color=self.no_color,
            )
            output_lines.append((q_time, line))

        # Sort all output lines chronologically
        def _to_utc_sort_key(item: tuple[Any, str]) -> datetime:
            t = item[0]
            if isinstance(t, datetime):
                if t.tzinfo is None:
                    return t.replace(tzinfo=UTC)
                return t.astimezone(UTC)
            return datetime.min.replace(tzinfo=UTC)

        output_lines.sort(key=_to_utc_sort_key)
        return [item[1] for item in output_lines]


def run_selftest(engine: Engine) -> int:
    """Run read-only database and query performance validation."""
    print("Running ReconBench pipeline-log selftest...", flush=True)
    failures = 0

    with engine.connect() as conn:
        # 1. current_user
        try:
            user = conn.execute(sa.text("SELECT current_user")).scalar()
            print(f"current_user: {user}", flush=True)
        except Exception as exc:
            print(f"current_user check FAILED: {type(exc).__name__}", flush=True)
            failures += 1

        # 2. rolsuper (must be false)
        try:
            rolsuper = conn.execute(
                sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            ).scalar()
            print(f"rolsuper: {rolsuper}", flush=True)
            if rolsuper is not False:
                print("rolsuper check FAILED: expected false", flush=True)
                failures += 1
        except Exception as exc:
            print(f"rolsuper check FAILED: {type(exc).__name__}", flush=True)
            failures += 1

        # 3. transaction_read_only (must be on)
        try:
            ro = conn.execute(sa.text("SHOW default_transaction_read_only")).scalar()
            print(f"transaction_read_only: {ro}", flush=True)
            if str(ro).strip().lower() != "on":
                print("transaction_read_only check FAILED: expected 'on'", flush=True)
                failures += 1
        except Exception as exc:
            print(f"transaction_read_only check FAILED: {type(exc).__name__}", flush=True)
            failures += 1

        # 4. statement_timeout
        try:
            st = conn.execute(sa.text("SHOW statement_timeout")).scalar()
            print(f"statement_timeout: {st}", flush=True)
        except Exception as exc:
            print(f"statement_timeout check FAILED: {type(exc).__name__}", flush=True)
            failures += 1

        # 5. alembic version_num
        try:
            vnum = conn.execute(sa.text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
            print(f"alembic version_num: {vnum} (expected: 0030)", flush=True)
        except Exception as exc:
            print(f"alembic version check FAILED: {type(exc).__name__}", flush=True)
            failures += 1

        # 6. Query timings over 24h window
        wid, _ = resolve_workspace(conn)
        db_now = get_db_now(conn)
        since_24h = db_now - timedelta(hours=24)

        queries_to_test = [
            ("query_file_events", lambda: query_file_events(conn, wid, since_24h)),
            ("query_doc_events", lambda: query_doc_events(conn, wid, since_24h)),
            ("query_vendor_resolutions", lambda: query_vendor_resolutions(conn, wid, since_24h)),
            ("query_ai_requests", lambda: query_ai_requests(conn, wid, since_24h)),
            ("query_link_events", lambda: query_link_events(conn, wid, since_24h)),
            ("query_user_actions", lambda: query_user_actions(conn, wid, since_24h)),
            (
                "query_recon_runs_and_cases",
                lambda: query_recon_runs_and_cases(conn, wid, since_24h),
            ),
            ("query_export_events", lambda: query_export_events(conn, wid, since_24h)),
            ("query_ai_health_events", lambda: query_ai_health_events(conn, wid, since_24h)),
            ("get_queue_counts", lambda: get_queue_counts(conn)),
        ]

        total_t0 = time.perf_counter()
        for name, fn in queries_to_test:
            q_t0 = time.perf_counter()
            res = fn()
            q_ms = (time.perf_counter() - q_t0) * 1000
            if isinstance(res, (list, dict)):
                rows = len(res)
            elif isinstance(res, tuple):
                rows = len(res[0]) + len(res[1])
            else:
                rows = 1
            print(f"{name}: {rows} rows, {q_ms:.1f}ms", flush=True)
        total_ms = (time.perf_counter() - total_t0) * 1000
        print(f"total cycle time: {total_ms:.1f}ms", flush=True)

    print(f"Selftest completed with {failures} failure(s).", flush=True)
    return 1 if failures > 0 else 0


def main() -> None:  # noqa: C901
    """Main watcher entry point."""
    parser = argparse.ArgumentParser(description="ReconBench pipeline log watcher")
    parser.add_argument("--once", action="store_true", help="Print recent events and exit")
    parser.add_argument(
        "--since", default="2h", help="Time window for --once mode (e.g. 2h, 24h, 30m)"
    )
    parser.add_argument(
        "--selftest", action="store_true", help="Run read-only verification checks"
    )
    parser.add_argument("--values", choices=["masked", "show"], help="Override values mode")
    parser.add_argument(
        "--ascii", action="store_true", help="ASCII output mode (no icons, Rs currency)"
    )
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colours")
    args = parser.parse_args()

    # Environment toggles and defaults
    raw_toggle = os.environ.get("RB_PIPELINE_LOG", "off").strip().lower()
    raw_values = os.environ.get("RB_PIPELINE_LOG_VALUES", "masked").strip().lower()
    raw_interval = os.environ.get("RB_PIPELINE_LOG_INTERVAL", "3").strip()
    raw_ascii = os.environ.get("RB_PIPELINE_LOG_ASCII", "0").strip()

    toggle_on = raw_toggle == "on"
    values_mode = "masked"
    if raw_values in ("masked", "show"):
        values_mode = raw_values
    else:
        print("⚠ unexpected RB_PIPELINE_LOG_VALUES; using default 'masked'", flush=True)

    try:
        interval_s = int(raw_interval)
        if interval_s < 2:
            interval_s = 2
    except ValueError:
        interval_s = 3
        print("⚠ unexpected RB_PIPELINE_LOG_INTERVAL; using default 3", flush=True)

    ascii_mode = raw_ascii == "1"

    # Command-line overrides
    if args.values:
        values_mode = args.values
    if args.ascii:
        ascii_mode = True
    no_color = args.no_color or bool(os.environ.get("NO_COLOR"))

    # When OFF and not in --once/--selftest: idle without database connection
    if not toggle_on and not args.once and not args.selftest:
        print(
            "pipeline log is OFF — set RB_PIPELINE_LOG=on in .env, then run: "
            "docker compose up -d --no-deps pipeline-log",
            flush=True,
        )
        while _RUNNING:
            time.sleep(1)
        sys.exit(0)

    # Initialize read-only trace engine
    engine = create_trace_engine()

    if args.selftest:
        sys.exit(run_selftest(engine))

    watcher = PipelineWatcher(
        engine=engine,
        values_mode=values_mode,
        interval_s=interval_s,
        ascii_mode=ascii_mode,
        no_color=no_color,
    )

    # Once mode
    if args.once:
        duration = parse_duration(args.since)
        with engine.connect() as conn:
            db_now = get_db_now(conn)
            since = db_now - duration
            lines = watcher.process_cycle(conn, since=since)
            for line in lines:
                watcher.print_line(line, event_time=since)
        sys.exit(0)

    # Follow mode
    backoff_seconds = [5, 10, 20, 40, 60]
    backoff_idx = 0

    # Startup banner
    with engine.connect() as conn:
        db_now = get_db_now(conn)
        watcher.last_watermark = db_now
        wid, _ = resolve_workspace(conn)
        watcher.workspace_id = wid
        page_reader, doc_count = get_banner_metadata(conn, wid)
        q_counts = get_queue_counts(conn)
        all_zero = all(todo == 0 and doing == 0 for todo, doing in q_counts.values())
        if all_zero:
            q_str = "queue idle"
        else:
            q_str = ", ".join(f"{q}:{t}" for q, (t, _) in q_counts.items() if t > 0)
        since_ist = to_ist(db_now).strftime("%d/%m/%Y %H:%M:%S")  # type: ignore[union-attr]

        banner = render_startup_banner(
            values_mode=values_mode,
            interval_s=interval_s,
            since_ist=since_ist,
            page_reader=page_reader,
            doc_count=doc_count,
            queue_status=q_str,
            ascii_mode=ascii_mode,
            no_color=no_color,
        )
        print(banner, flush=True)

    while _RUNNING:
        t0 = time.perf_counter()
        try:
            with engine.connect() as conn:
                since = watcher.last_watermark - timedelta(seconds=10)
                lines = watcher.process_cycle(conn, since=since)
                for line in lines:
                    watcher.print_line(line)
                watcher.last_watermark = get_db_now(conn)

            # Reconnection reset if back
            if backoff_idx > 0:
                ok_m = format_status_mark("✓", ascii_mode=ascii_mode, no_color=no_color)
                print(f"{ok_m} reconnected", flush=True)
                backoff_idx = 0

        except Exception as exc:
            # Print class name ONLY to avoid credential/connection details leakage
            warn_m = format_status_mark("⚠", ascii_mode=ascii_mode, no_color=no_color)
            print(f"{warn_m} database error ({type(exc).__name__})", flush=True)
            sleep_time = backoff_seconds[min(backoff_idx, len(backoff_seconds) - 1)]
            backoff_idx += 1
            time.sleep(sleep_time)
            continue

        elapsed = time.perf_counter() - t0
        if elapsed > 2.0:
            warn_m = format_status_mark("⚠", ascii_mode=ascii_mode, no_color=no_color)
            print(f"{warn_m} slow poll ({elapsed:.2f}s)", flush=True)
            watcher.interval_s = min(30, watcher.interval_s * 2)
        else:
            watcher.interval_s = watcher.base_interval_s

        # 10-minute idle heartbeat
        if time.time() - watcher.last_print_time >= 600.0:
            hb = render_heartbeat(
                watcher.last_event_time_str,
                queue_status="queue idle",
                ascii_mode=ascii_mode,
                no_color=no_color,
            )
            print(hb, flush=True)
            watcher.last_print_time = time.time()

        # Sleep interval in small steps to react to SIGTERM within 2s
        sleep_left = watcher.interval_s
        while sleep_left > 0 and _RUNNING:
            step = min(0.5, sleep_left)
            time.sleep(step)
            sleep_left -= step


if __name__ == "__main__":
    main()
