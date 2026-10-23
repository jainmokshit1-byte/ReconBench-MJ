"""Read-only SQL queries for ReconBench pipeline log watcher.

Strictly READ-ONLY. No writes, no index creation, no credentials leakage.
Explicitly names every column (NO SELECT *).
Avoids secret/token/password/hash/seal/cipher columns and tables.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

_LOGGER = logging.getLogger(__name__)


def get_db_now(conn: Connection) -> datetime:
    """Fetch current database clock timestamp in UTC."""
    return conn.execute(sa.text("SELECT now() AT TIME ZONE 'UTC'")).scalar_one()


def get_banner_metadata(conn: Connection, workspace_id: UUID) -> tuple[str, int]:
    """Fetch stored primary page reader and total documents count."""
    page_reader = "off"
    try:
        reader_query = sa.text(
            """
            SELECT primary_reader, reader_strategy, vision_enabled
            FROM ai_setting
            WHERE workspace_id = :wid
            LIMIT 1
            """
        )
        row = conn.execute(reader_query, {"wid": workspace_id}).mappings().one_or_none()
        if row:
            strat = str(row["reader_strategy"] or "off")
            p_reader = str(row["primary_reader"] or "off")
            page_reader = "off" if strat == "off" else p_reader
    except Exception as exc:
        _LOGGER.warning("get_banner_metadata ai_setting query failed: %s", type(exc).__name__)

    doc_count = 0
    try:
        cnt_query = sa.text("SELECT count(*) FROM document WHERE workspace_id = :wid")
        doc_count = int(conn.execute(cnt_query, {"wid": workspace_id}).scalar() or 0)
    except Exception as exc:
        _LOGGER.warning("get_banner_metadata document count failed: %s", type(exc).__name__)

    return page_reader, doc_count


def query_file_events(
    conn: Connection, workspace_id: UUID, since: datetime, limit: int = 100
) -> list[dict[str, Any]]:
    """Query recent source_file activities."""
    query = sa.text(
        """
        SELECT id, display_name, zone, format, size_bytes, page_count,
               status, error_code, docs_found, docs_new,
               uploaded_at, parse_finished_at, parse_started_at, queued_at
        FROM source_file
        WHERE workspace_id = :wid
          AND (uploaded_at >= :since OR parse_finished_at >= :since)
        ORDER BY coalesce(parse_finished_at, uploaded_at) ASC
        LIMIT :lim
        """
    )
    params = {"wid": workspace_id, "since": since, "lim": limit}
    rows = conn.execute(query, params).mappings().fetchall()
    return [dict(r) for r in rows]


def query_doc_events(
    conn: Connection, workspace_id: UUID, since: datetime, limit: int = 100
) -> list[dict[str, Any]]:
    """Query newly created or modified document versions."""
    query = sa.text(
        """
        SELECT dv.id AS version_id, dv.document_id, dv.seq, dv.doc_no, dv.doc_date,
               dv.extraction_method, dv.grand_total, dv.needs_verification,
               dv.verification_reasons, dv.derived_from_version_id, dv.created_by,
               dv.created_at,
               d.doc_type
        FROM document_version dv
        JOIN document d ON d.id = dv.document_id
        WHERE dv.workspace_id = :wid
          AND dv.created_at >= :since
        ORDER BY dv.created_at ASC
        LIMIT :lim
        """
    )
    params = {"wid": workspace_id, "since": since, "lim": limit}
    rows = conn.execute(query, params).mappings().fetchall()
    return [dict(r) for r in rows]


def query_field_evidence_summary(
    conn: Connection, workspace_id: UUID, version_ids: Sequence[UUID]
) -> dict[UUID, dict[str, Any]]:
    """Query field evidence counts and low confidence fields for given document versions."""
    if not version_ids:
        return {}
    query = sa.text(
        """
        SELECT document_version_id, field_name, confidence, source
        FROM field_evidence
        WHERE workspace_id = :wid
          AND document_version_id = ANY(:vids)
          AND doc_line_id IS NULL
        LIMIT 1000
        """
    )
    rows = conn.execute(
        query, {"wid": workspace_id, "vids": list(version_ids)}
    ).mappings().fetchall()

    summary: dict[UUID, dict[str, Any]] = {}
    for r in rows:
        vid = r["document_version_id"]
        if vid not in summary:
            summary[vid] = {
                "rule_count": 0,
                "ai_count": 0,
                "manual_count": 0,
                "low_conf": [],
            }
        src = str(r["source"]).lower()
        if src == "rule":
            summary[vid]["rule_count"] += 1
        elif src == "ai":
            summary[vid]["ai_count"] += 1
        elif src == "manual":
            summary[vid]["manual_count"] += 1

        conf = r["confidence"]
        if conf is not None and Decimal(str(conf)) < Decimal("0.70"):
            summary[vid]["low_conf"].append((str(r["field_name"]), float(conf)))

    return summary


def query_vendor_resolutions(
    conn: Connection, workspace_id: UUID, since: datetime, limit: int = 100
) -> list[dict[str, Any]]:
    """Query documents resolved to vendors within time window."""
    query = sa.text(
        """
        SELECT d.id AS document_id, d.doc_no, d.doc_type, d.vendor_id,
               d.vendor_rule, d.vendor_score, d.vendor_resolved_at,
               v.canonical_name AS vendor_name,
               dv.vendor_gstin
        FROM document d
        LEFT JOIN vendor v ON v.id = d.vendor_id
        LEFT JOIN document_version dv ON dv.id = d.current_version_id
        WHERE d.workspace_id = :wid
          AND d.vendor_resolved_at >= :since
        ORDER BY d.vendor_resolved_at ASC
        LIMIT :lim
        """
    )
    params = {"wid": workspace_id, "since": since, "lim": limit}
    rows = conn.execute(query, params).mappings().fetchall()
    return [dict(r) for r in rows]


def query_ai_requests(
    conn: Connection, workspace_id: UUID, since: datetime, limit: int = 100
) -> list[dict[str, Any]]:
    """Query AI requests and call logs."""
    query = sa.text(
        """
        SELECT r.id AS request_id, r.hook, r.provider, r.model, r.request_state,
               r.apply_outcome, r.error_code, r.skip_code, r.tokens_input,
               r.tokens_output, r.created_at, r.finished_at, r.subject_id,
               r.ai_cache_id,
               c.outcome AS call_outcome, c.latency_ms, c.tokens_in, c.tokens_out,
               d.id AS document_id, d.doc_no, d.doc_type
        FROM ai_request r
        LEFT JOIN ai_call_log c ON c.ai_request_id = r.id
        LEFT JOIN document_version dv ON dv.id = r.subject_id
        LEFT JOIN document d ON d.id = dv.document_id
        WHERE r.workspace_id = :wid
          AND r.shadow = false
          AND (r.created_at >= :since OR r.finished_at >= :since)
        ORDER BY coalesce(r.finished_at, r.created_at) ASC
        LIMIT :lim
        """
    )
    params = {"wid": workspace_id, "since": since, "lim": limit}
    rows = conn.execute(query, params).mappings().fetchall()
    return [dict(r) for r in rows]


def query_ai_suggestions_details(
    conn: Connection,
    workspace_id: UUID,
    cache_ids: Sequence[UUID],
    version_ids: Sequence[UUID],
) -> tuple[dict[UUID, list[dict[str, Any]]], dict[UUID, list[str]]]:
    """Query ai_cache suggestion fields and document_warning rejection codes."""
    cache_data: dict[UUID, list[dict[str, Any]]] = {}
    if cache_ids:
        query = sa.text(
            """
            SELECT id, response->'fields' AS fields
            FROM ai_cache
            WHERE workspace_id = :wid AND id = ANY(:cids)
            LIMIT 100
            """
        )
        params = {"wid": workspace_id, "cids": list(cache_ids)}
        rows = conn.execute(query, params).mappings().fetchall()
        for r in rows:
            flds = r["fields"]
            if isinstance(flds, list):
                cache_data[r["id"]] = flds

    warnings: dict[UUID, list[str]] = {}
    if version_ids:
        w_query = sa.text(
            """
            SELECT document_version_id, code
            FROM document_warning
            WHERE workspace_id = :wid AND document_version_id = ANY(:vids)
            LIMIT 500
            """
        )
        w_params = {"wid": workspace_id, "vids": list(version_ids)}
        w_rows = conn.execute(w_query, w_params).mappings().fetchall()
        for wr in w_rows:
            vid = wr["document_version_id"]
            if vid not in warnings:
                warnings[vid] = []
            warnings[vid].append(str(wr["code"]))

    return cache_data, warnings


def query_link_events(
    conn: Connection, workspace_id: UUID, since: datetime, limit: int = 200
) -> list[dict[str, Any]]:
    """Query link creations/confirmations and resolutions."""
    query = sa.text(
        """
        SELECT l.id AS link_id, l.link_type, l.level, l.from_document_id, l.from_line_no,
               l.to_document_id, l.state, l.method, l.confidence, l.created_at,
               s.doc_no AS from_doc_no, s.doc_type AS from_doc_type,
               t.doc_no AS to_doc_no, t.doc_type AS to_doc_type
        FROM link l
        JOIN document s ON s.id = l.from_document_id
        JOIN document t ON t.id = l.to_document_id
        WHERE l.workspace_id = :wid
          AND l.created_at >= :since
        ORDER BY l.created_at ASC
        LIMIT :lim
        """
    )
    params = {"wid": workspace_id, "since": since, "lim": limit}
    rows = conn.execute(query, params).mappings().fetchall()
    return [dict(r) for r in rows]


def query_link_resolutions(
    conn: Connection, workspace_id: UUID, since: datetime, limit: int = 100
) -> list[dict[str, Any]]:
    """Query link resolutions where unlinked or suggested."""
    query = sa.text(
        """
        SELECT lr.id, lr.document_id, lr.outcome, lr.detail_code, lr.candidates,
               lr.computed_at,
               d.doc_no, d.doc_type
        FROM link_resolution lr
        JOIN document d ON d.id = lr.document_id
        WHERE lr.workspace_id = :wid
          AND lr.computed_at >= :since
        ORDER BY lr.computed_at ASC
        LIMIT :lim
        """
    )
    params = {"wid": workspace_id, "since": since, "lim": limit}
    rows = conn.execute(query, params).mappings().fetchall()
    return [dict(r) for r in rows]


def query_user_actions(
    conn: Connection, workspace_id: UUID, since: datetime, limit: int = 100
) -> list[dict[str, Any]]:
    """Query person actions from case_action and audit_log."""
    actions: list[dict[str, Any]] = []

    # Case actions (approve, hold, reject, note, confirm_link)
    ca_query = sa.text(
        """
        SELECT ca.id, ca.action, ca.reason, ca.occurred_at, ca.invoice_id,
               u.display_name AS user_name,
               d.doc_no, d.doc_type
        FROM case_action ca
        JOIN app_user u ON u.id = ca.actor_id
        JOIN document d ON d.id = ca.invoice_id
        WHERE ca.workspace_id = :wid
          AND ca.occurred_at >= :since
        ORDER BY ca.occurred_at ASC
        LIMIT :lim
        """
    )
    ca_params = {"wid": workspace_id, "since": since, "lim": limit}
    ca_rows = conn.execute(ca_query, ca_params).mappings().fetchall()
    for r in ca_rows:
        actions.append({
            "id": f"ca-{r['id']}",
            "time": r["occurred_at"],
            "action": r["action"],
            "user_name": r["user_name"],
            "doc_no": r["doc_no"],
            "doc_type": r["doc_type"],
            "doc_id": r["invoice_id"],
            "reason": r["reason"],
            "kind": "case_action",
        })

    # Audit log (document.correct -> Use suggested / corrections)
    al_query = sa.text(
        """
        SELECT al.id, al.action, al.occurred_at, al.target_id, al.after_state, al.reason,
               u.display_name AS user_name,
               d.doc_no, d.doc_type
        FROM audit_log al
        JOIN app_user u ON u.id = al.actor_id
        JOIN document d ON d.id = al.target_id
        WHERE al.workspace_id = :wid
          AND al.action = 'document.correct'
          AND al.occurred_at >= :since
        ORDER BY al.occurred_at ASC
        LIMIT :lim
        """
    )
    al_params = {"wid": workspace_id, "since": since, "lim": limit}
    al_rows = conn.execute(al_query, al_params).mappings().fetchall()
    for r in al_rows:
        actions.append({
            "id": f"al-{r['id']}",
            "time": r["occurred_at"],
            "action": "document.correct",
            "user_name": r["user_name"],
            "doc_no": r["doc_no"],
            "doc_type": r["doc_type"],
            "doc_id": r["target_id"],
            "after_state": r["after_state"],
            "reason": r["reason"],
            "kind": "audit_correction",
        })

    actions.sort(key=lambda a: a["time"] or datetime.min)
    return actions


def query_recon_runs_and_cases(
    conn: Connection, workspace_id: UUID, since: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Query reconciliation runs and individual invoice cases."""
    runs_query = sa.text(
        """
        SELECT rr.id, rr.status, rr.status_counts, rr.started_at, rr.finished_at,
               rr.queued_at, rr.total_cases, rr.trigger_kind, rr.scope
        FROM recon_run rr
        WHERE rr.workspace_id = :wid
          AND (rr.queued_at >= :since OR rr.finished_at >= :since)
        ORDER BY coalesce(rr.finished_at, rr.started_at, rr.queued_at) ASC
        LIMIT 20
        """
    )
    runs = conn.execute(runs_query, {"wid": workspace_id, "since": since}).mappings().fetchall()

    cases_query = sa.text(
        """
        SELECT rc.id, rc.run_id, rc.invoice_id, rc.status, rc.reason_codes,
               rc.invoice_total, rc.expected_taxable, rc.variance, rc.created_at,
               d.doc_no
        FROM recon_case rc
        JOIN document d ON d.id = rc.invoice_id
        WHERE rc.workspace_id = :wid
          AND rc.created_at >= :since
        ORDER BY rc.created_at ASC
        LIMIT 200
        """
    )
    cases = conn.execute(cases_query, {"wid": workspace_id, "since": since}).mappings().fetchall()
    return [dict(r) for r in runs], [dict(c) for c in cases]


def query_export_events(
    conn: Connection, workspace_id: UUID, since: datetime, limit: int = 50
) -> list[dict[str, Any]]:
    """Query export creation and completion events."""
    query = sa.text(
        """
        SELECT id, status, case_count, error_code, created_at, built_at
        FROM export_file
        WHERE workspace_id = :wid
          AND (created_at >= :since OR built_at >= :since)
        ORDER BY coalesce(built_at, created_at) ASC
        LIMIT :lim
        """
    )
    params = {"wid": workspace_id, "since": since, "lim": limit}
    rows = conn.execute(query, params).mappings().fetchall()
    return [dict(r) for r in rows]


def query_ai_health_events(
    conn: Connection, workspace_id: UUID, since: datetime, limit: int = 50
) -> list[dict[str, Any]]:
    """Query AI provider connection state changes from ai_provider_config."""
    query = sa.text(
        """
        SELECT id, provider, enabled, connected_at, last_error_code,
               last_error_at, last_health_ok, last_health_at,
               last_test_ok, last_test_at, updated_at
        FROM ai_provider_config
        WHERE workspace_id = :wid
          AND (updated_at >= :since
               OR connected_at >= :since
               OR last_error_at >= :since
               OR last_health_at >= :since
               OR last_test_at >= :since)
        ORDER BY coalesce(last_error_at, connected_at, last_health_at, last_test_at, updated_at) ASC
        LIMIT :lim
        """
    )
    params = {"wid": workspace_id, "since": since, "lim": limit}
    rows = conn.execute(query, params).mappings().fetchall()
    return [dict(r) for r in rows]


def query_failed_jobs(conn: Connection, limit: int = 10) -> list[dict[str, Any]]:
    """Query failed procrastinate jobs (task name and error message, no stack trace)."""
    try:
        query = sa.text(
            """
            SELECT id, queue_name, task_name, status, attempts
            FROM procrastinate_jobs
            WHERE status = 'failed'
            ORDER BY id DESC
            LIMIT :lim
            """
        )
        rows = conn.execute(query, {"lim": limit}).mappings().fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        _LOGGER.warning("query_failed_jobs failed: %s", type(exc).__name__)
        return []
