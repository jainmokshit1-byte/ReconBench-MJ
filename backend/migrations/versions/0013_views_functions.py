"""7. Views and functions migration, sourced from Backend Schema §7."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0013"
down_revision = '0012'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 1819
    r"""-- One row per invoice: the current case with its effective status and its staleness.
CREATE VIEW v_case_current AS
SELECT cc.id AS case_current_id, cc.workspace_id, cc.invoice_id, cc.case_id, cc.run_id,
       cc.vendor_id, COALESCE(vn.merged_into, vn.id) AS effective_vendor_id,
       cc.invoice_date, cc.computed_status, cc.overlay, cc.effective_status,
       cc.severity_rank, cc.human_touched, cc.latest_action_id, cc.version,
       c.aged, c.pending_days, c.reason_codes, c.po_nos, c.grn_nos,
       c.invoice_taxable, c.invoice_total, c.expected_taxable, c.variance, c.variance_pct,
       c.payable_taxable, c.at_risk, c.at_risk_gst, c.blocked_value, c.payment_due_date,
       ( (vn.id IS NOT NULL AND vn.input_seq > c.vendor_seq)
         OR EXISTS (SELECT 1
                      FROM jsonb_each_text(c.po_seqs) AS s(po_id, seq)
                      JOIN document p ON p.id = s.po_id::uuid
                     WHERE p.input_seq > s.seq::bigint)
         OR r.config_version_id IS DISTINCT FROM ca.config_version_id ) AS is_stale
  FROM case_current cc
  JOIN recon_case c        ON c.id = cc.case_id
  JOIN recon_run r         ON r.id = c.run_id
  LEFT JOIN vendor vn      ON vn.id = c.vendor_id
  LEFT JOIN config_active ca ON ca.workspace_id = cc.workspace_id;

-- Mapping screen: per-vendor counts and link health. The Unassigned bucket is vendor_id NULL.
CREATE VIEW v_vendor_link_health AS
WITH live AS (
  SELECT d.id, d.workspace_id, d.doc_type, COALESCE(v.merged_into, v.id) AS vendor_id
    FROM document d
    LEFT JOIN vendor v ON v.id = d.vendor_id
   WHERE d.withdrawn_at IS NULL
),
src AS (      -- one row per invoice or GRN: does it have a firm or a suggested PO link?
  SELECT lk.from_document_id AS document_id,
         bool_or(lk.state IN ('auto','confirmed')) AS has_firm,
         bool_or(lk.state = 'suggested')           AS has_suggested
    FROM link_current lc
    JOIN link lk ON lk.id = lc.current_link_id
   WHERE lk.link_type IN ('inv_po','grn_po')
   GROUP BY lk.from_document_id
),
po_used AS (  -- POs that at least one invoice or GRN points at
  SELECT DISTINCT lk.to_document_id AS po_id
    FROM link_current lc
    JOIN link lk ON lk.id = lc.current_link_id
   WHERE lk.link_type IN ('inv_po','grn_po') AND lk.state <> 'rejected'
)
SELECT l.workspace_id, l.vendor_id,
       count(*) FILTER (WHERE l.doc_type = 'po')      AS po_count,
       count(*) FILTER (WHERE l.doc_type = 'grn')     AS grn_count,
       count(*) FILTER (WHERE l.doc_type = 'invoice') AS invoice_count,
       count(*) FILTER (WHERE l.doc_type IN ('invoice','grn') AND COALESCE(s.has_firm, false)) AS linked_count,
       count(*) FILTER (WHERE l.doc_type IN ('invoice','grn') AND NOT COALESCE(s.has_firm, false)
                                                              AND COALESCE(s.has_suggested, false)) AS suggested_count,
       count(*) FILTER (WHERE l.doc_type IN ('invoice','grn') AND NOT COALESCE(s.has_firm, false)
                                                              AND NOT COALESCE(s.has_suggested, false)) AS unlinked_count,
       count(*) FILTER (WHERE l.doc_type = 'po' AND u.po_id IS NULL) AS orphan_count
  FROM live l
  LEFT JOIN src s     ON s.document_id = l.id
  LEFT JOIN po_used u ON u.po_id = l.id
 GROUP BY l.workspace_id, l.vendor_id;

-- GRNI now: accepted but not yet billed, at PO rate (TRD §11.3). Not filtered by period.
CREATE VIEW v_grni_now AS
SELECT g.workspace_id, g.po_document_id, g.po_line_no, d.vendor_id,
       g.grni_qty, g.po_rate, round(g.grni_qty * g.po_rate, 2) AS grni_value
  FROM po_line_ledger g
  JOIN document d ON d.id = g.po_document_id
 WHERE d.withdrawn_at IS NULL AND g.grni_qty > 0;

-- Excel Summary sheet and run history: counts and rupee totals per run.
CREATE VIEW v_run_summary AS
SELECT r.id AS run_id, r.workspace_id, r.status, r.finished_at, r.engine_version, r.rules_hash,
       r.config_version_id, r.ai_mode, r.ai_provider, r.ai_model,
       count(c.id)                                              AS invoices,
       count(*) FILTER (WHERE c.status = 'duplicate')           AS duplicate,
       count(*) FILTER (WHERE c.status = 'unlinked')            AS unlinked,
       count(*) FILTER (WHERE c.status = 'needs_review')        AS needs_review,
       count(*) FILTER (WHERE c.status = 'exception')           AS exception,
       count(*) FILTER (WHERE c.status = 'pending')             AS pending,
       count(*) FILTER (WHERE c.status = 'matched_flags')       AS matched_flags,
       count(*) FILTER (WHERE c.status = 'matched')             AS matched,
       COALESCE(sum(c.invoice_taxable), 0)  AS invoiced_taxable,
       COALESCE(sum(c.payable_taxable), 0)  AS payable_taxable,
       COALESCE(sum(c.at_risk), 0)          AS at_risk,
       COALESCE(sum(c.at_risk_gst), 0)      AS at_risk_gst,
       COALESCE(sum(c.blocked_value), 0)    AS blocked_value
  FROM recon_run r
  LEFT JOIN recon_case c ON c.run_id = r.id
 GROUP BY r.id;

-- Home KPI cards for an invoice-date period. Effective status throughout (TRD §11.3).
CREATE FUNCTION kpi_summary(p_workspace UUID, p_from DATE, p_to DATE)
RETURNS TABLE (invoice_count BIGINT, matched_pct NUMERIC, straight_through_pct NUMERIC,
               pending_count BIGINT, exception_count BIGINT, unlinked_count BIGINT,
               at_risk_total NUMERIC, at_risk_gst_total NUMERIC, blocked_total NUMERIC, grni_total NUMERIC)
LANGUAGE sql STABLE AS $$
  WITH c AS (
    SELECT * FROM v_case_current
     WHERE workspace_id = p_workspace AND invoice_date BETWEEN p_from AND p_to
  )
  SELECT count(*),
         round(100.0 * count(*) FILTER (WHERE effective_status IN ('matched','matched_flags')) / NULLIF(count(*), 0), 2),
         round(100.0 * count(*) FILTER (WHERE computed_status = 'matched' AND NOT human_touched) / NULLIF(count(*), 0), 2),
         count(*) FILTER (WHERE effective_status = 'pending'),
         count(*) FILTER (WHERE effective_status = 'exception'),
         count(*) FILTER (WHERE effective_status = 'unlinked'),
         COALESCE(sum(at_risk), 0),
         COALESCE(sum(at_risk_gst), 0),
         COALESCE(sum(blocked_value), 0),
         (SELECT COALESCE(sum(g.grni_value), 0) FROM v_grni_now g WHERE g.workspace_id = p_workspace)
    FROM c
$$;
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
