"""Read-only database queries for the ReconBench terminal trace tool.

ALL queries are:
- Strictly SELECT only (never write or lock).
- Fully parameterized with SQLAlchemy text().
- Bounded with explicit LIMIT clauses.
- Workspace-scoped (where applicable).
- Exception-safe (never leak credentials / DSN).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

_LOGGER = logging.getLogger(__name__)


def _clean_str(val: Any) -> str:
    return str(val) if val is not None else ""


# --- Workspace Resolution ---


def resolve_workspace(conn: Connection, workspace_id: UUID | None = None) -> tuple[UUID, str]:
    """Find the target workspace or raise ValueError if ambiguous/not found."""
    try:
        if workspace_id is not None:
            query = sa.text("SELECT id, name FROM workspace WHERE id = :wid LIMIT 1")
            row = conn.execute(query, {"wid": workspace_id}).mappings().one_or_none()
            if row is None:
                raise ValueError(f"Workspace {workspace_id} not found.")
            return UUID(str(row["id"])), str(row["name"])

        # Fetch up to 2 workspaces to check uniqueness
        query = sa.text("SELECT id, name FROM workspace ORDER BY id LIMIT 2")
        rows = conn.execute(query).mappings().fetchall()
        if not rows:
            raise ValueError("No workspaces found in the database.")
        if len(rows) > 1:
            raise ValueError(
                "Multiple workspaces exist. Please specify one with --workspace-id <uuid>."
            )
        return UUID(str(rows[0]["id"])), str(rows[0]["name"])
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        _LOGGER.warning("Workspace resolution query failed: %s", type(exc).__name__)
        raise ValueError("Could not query workspace table (access error or busy).") from None


def list_workspaces(conn: Connection) -> list[tuple[UUID, str]]:
    """List available workspaces (bounded to 20)."""
    try:
        query = sa.text("SELECT id, name FROM workspace ORDER BY name LIMIT 20")
        rows = conn.execute(query).mappings().fetchall()
        return [(UUID(str(r["id"])), str(r["name"])) for r in rows]
    except Exception:
        return []


# --- Document Lookup ---


def find_document(
    conn: Connection, workspace_id: UUID, identifier: str
) -> dict[str, Any] | None:
    """Find document by UUID, doc_no or canonical_doc_no."""
    ident = identifier.strip()
    is_uuid = False
    doc_uuid: UUID | None = None
    try:
        doc_uuid = UUID(ident)
        is_uuid = True
    except ValueError:
        pass

    canonical = re.sub(r"[^A-Za-z0-9]", "", ident).upper()

    try:
        if is_uuid:
            query = sa.text(
                """
                SELECT d.id, d.workspace_id, d.doc_type, d.doc_no, d.canonical_doc_no,
                       d.doc_date, d.grand_total, d.vendor_id, d.vendor_rule, d.vendor_score,
                       d.vendor_locked, d.current_version_id, d.first_seen_at, d.updated_at,
                       v.canonical_name AS vendor_name, v.anchor AS vendor_anchor
                FROM document d
                LEFT JOIN vendor v ON v.id = d.vendor_id
                WHERE d.workspace_id = :wid
                  AND (d.id = :uid OR d.doc_no = :ident OR d.canonical_doc_no = :canon)
                LIMIT 1
                """
            )
            params = {"wid": workspace_id, "uid": doc_uuid, "ident": ident, "canon": canonical}
        else:
            query = sa.text(
                """
                SELECT d.id, d.workspace_id, d.doc_type, d.doc_no, d.canonical_doc_no,
                       d.doc_date, d.grand_total, d.vendor_id, d.vendor_rule, d.vendor_score,
                       d.vendor_locked, d.current_version_id, d.first_seen_at, d.updated_at,
                       v.canonical_name AS vendor_name, v.anchor AS vendor_anchor
                FROM document d
                LEFT JOIN vendor v ON v.id = d.vendor_id
                WHERE d.workspace_id = :wid
                  AND (d.doc_no = :ident OR d.canonical_doc_no = :canon)
                ORDER BY d.first_seen_at DESC
                LIMIT 1
                """
            )
            params = {"wid": workspace_id, "ident": ident, "canon": canonical}

        row = conn.execute(query, params).mappings().one_or_none()
        return dict(row) if row else None
    except Exception as exc:
        _LOGGER.warning("find_document query failed: %s", type(exc).__name__)
        return None


# --- Ingestion & Document Details ---


def get_document_version(
    conn: Connection, workspace_id: UUID, version_id: UUID
) -> dict[str, Any] | None:
    """Fetch document_version details."""
    try:
        query = sa.text(
            """
            SELECT dv.id, dv.document_id, dv.seq, dv.doc_no, dv.doc_date,
                   dv.vendor_name, dv.vendor_gstin, dv.buyer_gstin,
                   dv.taxable_total, dv.cgst_total, dv.sgst_total, dv.igst_total,
                   dv.round_off, dv.grand_total, dv.po_refs, dv.grn_refs,
                   dv.extraction_method, dv.extraction_conf, dv.needs_verification,
                   dv.verification_reasons, dv.source_file_id, dv.parser_version,
                   dv.created_at
            FROM document_version dv
            WHERE dv.workspace_id = :wid AND dv.id = :vid
            LIMIT 1
            """
        )
        row = conn.execute(query, {"wid": workspace_id, "vid": version_id}).mappings().one_or_none()
        return dict(row) if row else None
    except Exception as exc:
        _LOGGER.warning("get_document_version failed: %s", type(exc).__name__)
        return None


def get_source_file(
    conn: Connection, workspace_id: UUID, source_file_id: UUID
) -> dict[str, Any] | None:
    """Fetch source_file metadata."""
    try:
        query = sa.text(
            """
            SELECT id, display_name, format, status, error_code, error_message,
                   size_bytes, page_count, uploaded_at, parse_started_at, parse_finished_at
            FROM source_file
            WHERE workspace_id = :wid AND id = :fid
            LIMIT 1
            """
        )
        row = conn.execute(query, {"wid": workspace_id, "fid": source_file_id}).mappings().one_or_none()
        return dict(row) if row else None
    except Exception as exc:
        _LOGGER.warning("get_source_file failed: %s", type(exc).__name__)
        return None


def get_field_evidence(
    conn: Connection, workspace_id: UUID, version_id: UUID
) -> dict[str, dict[str, Any]]:
    """Fetch field_evidence for header fields."""
    evidence_by_field: dict[str, dict[str, Any]] = {}
    try:
        query = sa.text(
            """
            SELECT field_name, value_text, confidence, source, origin, verification
            FROM field_evidence
            WHERE workspace_id = :wid
              AND document_version_id = :vid
              AND doc_line_id IS NULL
            LIMIT 50
            """
        )
        rows = conn.execute(query, {"wid": workspace_id, "vid": version_id}).mappings().fetchall()
        for r in rows:
            name = str(r["field_name"])
            evidence_by_field[name] = dict(r)
    except Exception as exc:
        _LOGGER.warning("get_field_evidence failed: %s", type(exc).__name__)
    return evidence_by_field


def get_doc_lines(
    conn: Connection, workspace_id: UUID, version_id: UUID, limit: int = 50
) -> list[dict[str, Any]]:
    """Fetch doc_line items."""
    try:
        query = sa.text(
            """
            SELECT id, line_no, description, quantity, rate, amount,
                   uom, hsn_sac, po_ref
            FROM doc_line
            WHERE workspace_id = :wid AND document_version_id = :vid
            ORDER BY line_no ASC
            LIMIT :lim
            """
        )
        rows = conn.execute(query, {"wid": workspace_id, "vid": version_id, "lim": limit}).mappings().fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        _LOGGER.warning("get_doc_lines failed: %s", type(exc).__name__)
        return []


# --- AI Reading & Suggestions ---


def get_ai_reading(
    conn: Connection, workspace_id: UUID, document_id: UUID, version_id: UUID
) -> dict[str, Any] | None:
    """Fetch latest AI request, call log, and cached response for this document/version."""
    try:
        # Find latest AI request for this version or document
        query = sa.text(
            """
            SELECT r.id, r.hook, r.provider, r.model, r.prompt_version, r.request_state,
                   r.apply_outcome, r.cache_key, r.error_code, r.skip_code, r.created_at,
                   c.outcome AS log_outcome, c.error_code AS log_error_code,
                   c.latency_ms, c.tokens_in, c.tokens_out
            FROM ai_request r
            LEFT JOIN ai_call_log c ON c.ai_request_id = r.id
            WHERE r.workspace_id = :wid
              AND r.subject_id = :vid
              AND r.shadow = false
            ORDER BY r.created_at DESC, r.attempt_no DESC
            LIMIT 1
            """
        )
        row = conn.execute(query, {"wid": workspace_id, "vid": version_id}).mappings().one_or_none()

        if row is None:
            # Fallback: check any version of this document
            query_fallback = sa.text(
                """
                SELECT r.id, r.hook, r.provider, r.model, r.prompt_version, r.request_state,
                       r.apply_outcome, r.cache_key, r.error_code, r.skip_code, r.created_at,
                       c.outcome AS log_outcome, c.error_code AS log_error_code,
                       c.latency_ms, c.tokens_in, c.tokens_out
                FROM ai_request r
                JOIN document_version dv ON dv.id = CAST(r.subject_id AS UUID)
                LEFT JOIN ai_call_log c ON c.ai_request_id = r.id
                WHERE r.workspace_id = :wid
                  AND dv.document_id = :did
                  AND r.shadow = false
                ORDER BY r.created_at DESC, r.attempt_no DESC
                LIMIT 1
                """
            )
            row = conn.execute(query_fallback, {"wid": workspace_id, "did": document_id}).mappings().one_or_none()

        if row is None:
            return None

        result: dict[str, Any] = {
            "request_id": row["id"],
            "provider": row["provider"],
            "model": row["model"],
            "state": row["request_state"],
            "outcome": row["apply_outcome"] or row["log_outcome"],
            "error_code": row["error_code"] or row["log_error_code"],
            "skip_code": row["skip_code"],
            "latency_ms": row["latency_ms"],
            "tokens_in": row["tokens_in"],
            "tokens_out": row["tokens_out"],
            "created_at": row["created_at"],
            "suggestion_count": 0,
            "handwritten_refs": [],
            "fields": {},
            "lines": [],
        }

        # Query cache response if cache_key exists
        cache_key = row["cache_key"]
        if cache_key:
            cache_query = sa.text(
                """
                SELECT response
                FROM ai_cache
                WHERE workspace_id = :wid AND cache_key = :ck
                LIMIT 1
                """
            )
            cache_row = conn.execute(cache_query, {"wid": workspace_id, "ck": cache_key}).mappings().one_or_none()
            if cache_row and isinstance(cache_row["response"], dict):
                resp = cache_row["response"]
                fields_list = resp.get("fields") or []
                lines_list = resp.get("lines") or []
                annotations = resp.get("annotations") or []

                sugg_count = len(fields_list)
                for line in lines_list:
                    sugg_count += len(line.get("cells") or [])

                result["suggestion_count"] = sugg_count

                # Extract handwritten / stamped refs
                hw_refs: list[dict[str, str]] = []
                for ann in annotations:
                    kind = str(ann.get("looks_like") or ann.get("kind") or "").lower()
                    text = str(ann.get("text") or "").strip()
                    if not text:
                        continue
                    if "po" in kind or "po" in text.lower():
                        hw_refs.append({"type": "PO", "text": text})
                    elif "grn" in kind or "grn" in text.lower():
                        hw_refs.append({"type": "GRN", "text": text})
                result["handwritten_refs"] = hw_refs

                # Map suggested fields by name for comparison
                f_map: dict[str, str] = {}
                for f in fields_list:
                    fname = f.get("name")
                    fval = f.get("text")
                    if fname and fval is not None:
                        f_map[fname] = str(fval)
                result["fields"] = f_map
                result["lines"] = lines_list

        return result
    except Exception as exc:
        _LOGGER.warning("get_ai_reading failed: %s", type(exc).__name__)
        return None


# --- Links & Ledger ---


def get_document_links(
    conn: Connection, workspace_id: UUID, document_id: UUID
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch current links for this document, and any GRN->PO links for linked POs."""
    doc_links: list[dict[str, Any]] = []
    grn_to_po_links: list[dict[str, Any]] = []
    po_ids: set[UUID] = set()

    try:
        # Document links (active via link_current)
        query = sa.text(
            """
            SELECT l.id, l.link_type, l.level, l.from_document_id, l.from_line_no,
                   l.to_document_id, l.state, l.confidence, l.method,
                   l.evidence_snippet, l.detail_code,
                   s.doc_no AS from_doc_no, s.doc_type AS from_doc_type,
                   t.doc_no AS to_doc_no, t.doc_type AS to_doc_type
            FROM link_current lc
            JOIN link l ON l.id = lc.current_link_id
            JOIN document s ON s.id = l.from_document_id
            JOIN document t ON t.id = l.to_document_id
            WHERE lc.workspace_id = :wid
              AND (lc.from_document_id = :did OR lc.to_document_id = :did)
            ORDER BY l.link_type, l.from_line_no NULLS FIRST, l.created_at DESC
            LIMIT 50
            """
        )
        rows = conn.execute(query, {"wid": workspace_id, "did": document_id}).mappings().fetchall()
        for r in rows:
            d = dict(r)
            # Normalize target info
            if d["from_document_id"] == document_id:
                d["target_doc_no"] = d["to_doc_no"]
                d["target_doc_type"] = d["to_doc_type"]
                if d["to_doc_type"] == "po":
                    po_ids.add(UUID(str(d["to_document_id"])))
            else:
                d["target_doc_no"] = d["from_doc_no"]
                d["target_doc_type"] = d["from_doc_type"]
                if d["from_doc_type"] == "po":
                    po_ids.add(UUID(str(d["from_document_id"])))
            doc_links.append(d)

        # If linked to any POs, fetch active GRN->PO links for those POs
        seen_grn_ids: set[UUID] = set()
        for po_id in po_ids:
            grn_query = sa.text(
                """
                SELECT l.id, l.link_type, l.level, l.from_document_id, l.from_line_no,
                       l.to_document_id,
                       l.state, l.confidence, l.method,
                       s.doc_no AS source_doc_no, t.doc_no AS target_doc_no
                FROM link_current lc
                JOIN link l ON l.id = lc.current_link_id
                JOIN document s ON s.id = l.from_document_id
                JOIN document t ON t.id = l.to_document_id
                WHERE lc.workspace_id = :wid
                  AND l.link_type = 'grn_po'
                  AND l.to_document_id = :poid
                ORDER BY l.created_at DESC
                LIMIT 10
                """
            )
            g_rows = conn.execute(grn_query, {"wid": workspace_id, "poid": po_id}).mappings().fetchall()
            for gr in g_rows:
                gr_dict = dict(gr)
                gr_id = gr_dict.get("id")
                if gr_id and gr_id in seen_grn_ids:
                    continue
                if gr_id:
                    seen_grn_ids.add(gr_id)
                grn_to_po_links.append(gr_dict)

    except Exception as exc:
        _LOGGER.warning("get_document_links failed: %s", type(exc).__name__)

    return doc_links, grn_to_po_links


def get_po_ledger(
    conn: Connection, workspace_id: UUID, po_ids: Sequence[UUID]
) -> list[dict[str, Any]]:
    """Fetch current po_line_ledger rows for given POs."""
    if not po_ids:
        return []
    try:
        query = sa.text(
            """
            SELECT po_document_id, po_line_no, ordered, received_accepted,
                   received_rejected, billed, open_qty, computed_at
            FROM po_line_ledger
            WHERE workspace_id = :wid AND po_document_id IN :poids
            ORDER BY po_document_id, po_line_no ASC
            LIMIT 50
            """
        ).bindparams(sa.bindparam("poids", expanding=True))
        rows = conn.execute(query, {"wid": workspace_id, "poids": list(po_ids)}).mappings().fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        _LOGGER.warning("get_po_ledger failed: %s", type(exc).__name__)
        return []


# --- Reconciliation Case & Line Results ---


def get_recon_case_and_lines(
    conn: Connection, workspace_id: UUID, invoice_id: UUID
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Fetch latest reconciliation case and line results for an invoice."""
    try:
        case_query = sa.text(
            """
            SELECT rc.id, rc.run_id, rc.status, rc.reason_codes, rc.invoice_taxable,
                   rc.invoice_total, rc.expected_taxable, rc.variance, rc.payable_taxable,
                   rc.at_risk, rc.blocked_value, rc.created_at,
                   rr.run_date, rr.status AS run_status
            FROM recon_case rc
            JOIN recon_run rr ON rr.id = rc.run_id
            WHERE rc.workspace_id = :wid AND rc.invoice_id = :iid
            ORDER BY rc.created_at DESC
            LIMIT 1
            """
        )
        case_row = conn.execute(case_query, {"wid": workspace_id, "iid": invoice_id}).mappings().one_or_none()
        if not case_row:
            return None, []

        case_info = dict(case_row)
        case_id = case_info["id"]

        lines_query = sa.text(
            """
            SELECT line_no, line_type, status, po_qty, po_rate, billed_qty,
                   invoice_net_rate, payable_rate, grn_accepted_cum, reason_codes
            FROM recon_line_result
            WHERE workspace_id = :wid AND case_id = :cid
            ORDER BY line_no ASC
            LIMIT 50
            """
        )
        line_rows = conn.execute(lines_query, {"wid": workspace_id, "cid": case_id}).mappings().fetchall()
        return case_info, [dict(r) for r in line_rows]
    except Exception as exc:
        _LOGGER.warning("get_recon_case_and_lines failed: %s", type(exc).__name__)
        return None, []


# --- Run Mode Queries ---


def get_recon_run_summary(
    conn: Connection, workspace_id: UUID, run_identifier: str = "latest"
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Fetch run metadata and all cases for that run."""
    ident = run_identifier.strip()
    try:
        if ident.lower() == "latest":
            run_query = sa.text(
                """
                SELECT id, status, run_date, started_at, finished_at,
                       total_cases, done_cases, status_counts, rules_hash, engine_version
                FROM recon_run
                WHERE workspace_id = :wid
                ORDER BY queued_at DESC
                LIMIT 1
                """
            )
            run_row = conn.execute(run_query, {"wid": workspace_id}).mappings().one_or_none()
        else:
            # Check if UUID or short hex
            clean_id = ident.lstrip("Rr-")
            run_query = sa.text(
                """
                SELECT id, status, run_date, started_at, finished_at,
                       total_cases, done_cases, status_counts, rules_hash, engine_version
                FROM recon_run
                WHERE workspace_id = :wid
                  AND (CAST(id AS TEXT) LIKE :prefix)
                ORDER BY queued_at DESC
                LIMIT 1
                """
            )
            run_row = conn.execute(run_query, {"wid": workspace_id, "prefix": f"{clean_id}%"}).mappings().one_or_none()

        if not run_row:
            return None, []

        run_info = dict(run_row)
        run_id = run_info["id"]

        cases_query = sa.text(
            """
            SELECT rc.id, rc.invoice_id, rc.status, rc.reason_codes,
                   rc.invoice_total, rc.variance, rc.at_risk,
                   d.doc_no
            FROM recon_case rc
            LEFT JOIN document d ON d.id = rc.invoice_id
            WHERE rc.workspace_id = :wid AND rc.run_id = :rid
            ORDER BY rc.created_at ASC
            LIMIT 200
            """
        )
        case_rows = conn.execute(cases_query, {"wid": workspace_id, "rid": run_id}).mappings().fetchall()
        return run_info, [dict(r) for r in case_rows]
    except Exception as exc:
        _LOGGER.warning("get_recon_run_summary failed: %s", type(exc).__name__)
        return None, []


# --- Queues & Follow Queries ---


def get_queue_counts(conn: Connection) -> dict[str, tuple[int, int]]:
    """Count todo/doing jobs per queue from procrastinate_jobs."""
    result: dict[str, tuple[int, int]] = {
        "ingest": (0, 0),
        "parse": (0, 0),
        "mapping": (0, 0),
        "reconcile": (0, 0),
        "exports": (0, 0),
        "ai": (0, 0),
    }
    try:
        query = sa.text(
            """
            SELECT queue_name, status, count(*) AS cnt
            FROM procrastinate_jobs
            WHERE status IN ('todo', 'doing')
            GROUP BY queue_name, status
            LIMIT 50
            """
        )
        rows = conn.execute(query).mappings().fetchall()
        temp: dict[str, dict[str, int]] = {}
        for r in rows:
            q = str(r["queue_name"])
            st = str(r["status"])
            cnt = int(r["cnt"])
            if q not in temp:
                temp[q] = {"todo": 0, "doing": 0}
            temp[q][st] = cnt

        for q in result:
            t = temp.get(q, {})
            result[q] = (t.get("todo", 0), t.get("doing", 0))
    except Exception as exc:
        _LOGGER.warning("get_queue_counts failed: %s", type(exc).__name__)

    return result


def get_recent_follow_events(
    conn: Connection, workspace_id: UUID, since: datetime, limit: int = 50
) -> list[dict[str, Any]]:
    """Poll for recent activities across files, AI, links, and runs."""
    events: list[dict[str, Any]] = []

    try:
        # 1. Processing events / files parsed or failed
        file_query = sa.text(
            """
            SELECT pe.id, pe.stage, pe.outcome, pe.occurred_at,
                   sf.display_name, sf.docs_found, sf.docs_new, sf.error_code
            FROM processing_event pe
            JOIN source_file sf ON sf.id = pe.source_file_id
            WHERE pe.workspace_id = :wid
              AND pe.occurred_at >= :since
            ORDER BY pe.occurred_at ASC
            LIMIT :lim
            """
        )
        f_rows = conn.execute(file_query, {"wid": workspace_id, "since": since, "lim": limit}).mappings().fetchall()
        for r in f_rows:
            stage = r["stage"]
            name = r["display_name"] or "file"
            time = r["occurred_at"]
            if stage == "parsed":
                docs = r["docs_found"] or r["docs_new"] or 0
                events.append({
                    "id": f"pe-{r['id']}",
                    "time": time,
                    "name": name,
                    "kind": "file_parsed",
                    "detail": f"{docs} doc{'s' if docs != 1 else ''}",
                })
            elif stage == "received":
                events.append({
                    "id": f"pe-{r['id']}",
                    "time": time,
                    "name": name,
                    "kind": "file_queued",
                    "detail": "queued for parsing",
                })

        # 2. AI reading events
        ai_query = sa.text(
            """
            SELECT r.id, r.provider, r.model, r.request_state, r.created_at,
                   d.doc_no
            FROM ai_request r
            JOIN document_version dv ON dv.id = CAST(r.subject_id AS UUID)
            JOIN document d ON d.id = dv.document_id
            WHERE r.workspace_id = :wid
              AND r.created_at >= :since
              AND r.shadow = false
            ORDER BY r.created_at ASC
            LIMIT :lim
            """
        )
        ai_rows = conn.execute(ai_query, {"wid": workspace_id, "since": since, "lim": limit}).mappings().fetchall()
        for r in ai_rows:
            st = r["request_state"]
            doc_name = r["doc_no"] or "invoice"
            events.append({
                "id": f"ai-{r['id']}",
                "time": r["created_at"],
                "name": doc_name,
                "kind": "ai_queued" if st in ("queued", "reserved") else "ai_done",
                "detail": f"{r['provider']}/{r['model']} · {st}",
            })

        # 3. New reconciliation runs
        run_query = sa.text(
            """
            SELECT id, status, status_counts, started_at, finished_at, queued_at
            FROM recon_run
            WHERE workspace_id = :wid
              AND queued_at >= :since
            ORDER BY queued_at ASC
            LIMIT :lim
            """
        )
        run_rows = conn.execute(run_query, {"wid": workspace_id, "since": since, "lim": limit}).mappings().fetchall()
        for r in run_rows:
            run_id = str(r["id"])[:8]
            counts = r["status_counts"] or {}
            detail = f"Matched {counts.get('matched', 0)} · Needs review {counts.get('needs_review', 0)}"
            events.append({
                "id": f"rr-{r['id']}",
                "time": r["finished_at"] or r["started_at"] or r["queued_at"],
                "name": f"run R-{run_id}",
                "kind": "run_done",
                "detail": detail,
            })

    except Exception as exc:
        _LOGGER.warning("get_recent_follow_events failed: %s", type(exc).__name__)

    # Sort events by time
    events.sort(key=lambda e: e.get("time") or datetime.min)
    return events
