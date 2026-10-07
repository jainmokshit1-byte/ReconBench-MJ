"""Count undated invoices in the Home KPI summary.

An invoice whose date could not be read (an unreadable scan) is always part of
the period's export workbook (docs/decision-log.md D19 #5). ``kpi_summary``
filtered on ``invoice_date BETWEEN``, which drops NULL dates, so Home showed
fewer invoices than the Excel Summary for the same period. The function keeps
its signature, owner (reconbench_migrator, revision 0015) and grants.
"""

# ruff: noqa: E501 -- keep the function body identical in shape to revision 0013.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_block(
        r"""CREATE OR REPLACE FUNCTION kpi_summary(p_workspace UUID, p_from DATE, p_to DATE)
RETURNS TABLE (invoice_count BIGINT, matched_pct NUMERIC, straight_through_pct NUMERIC,
               pending_count BIGINT, exception_count BIGINT, unlinked_count BIGINT,
               at_risk_total NUMERIC, at_risk_gst_total NUMERIC, blocked_total NUMERIC, grni_total NUMERIC)
LANGUAGE sql STABLE AS $$
  WITH c AS (
    SELECT * FROM v_case_current
     WHERE workspace_id = p_workspace
       AND (invoice_date IS NULL OR invoice_date BETWEEN p_from AND p_to)
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
$$;"""
    )


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
