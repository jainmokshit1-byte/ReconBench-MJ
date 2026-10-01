"""3. Setup migration, sourced from Backend Schema §3."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 171
    r"""CREATE EXTENSION IF NOT EXISTS pg_trgm;          -- trigram pickers (TRD §4, §6.4)

-- Numeric domains (TR-DAT-3) ---------------------------------------------------
CREATE DOMAIN d_amount  AS NUMERIC(18,2);
CREATE DOMAIN d_qty     AS NUMERIC(18,4);        -- quantities and unit rates
CREATE DOMAIN d_score   AS NUMERIC(5,4)  CHECK (VALUE >= 0 AND VALUE <= 1);
CREATE DOMAIN d_taxrate AS NUMERIC(5,2)  CHECK (VALUE >= 0 AND VALUE <= 100);
CREATE DOMAIN d_hash    AS TEXT          CHECK (VALUE ~ '^[0-9a-f]{64}$');   -- hex SHA-256

-- Documents and files ----------------------------------------------------------
CREATE TYPE doc_type          AS ENUM ('po','grn','invoice');
CREATE TYPE upload_zone       AS ENUM ('po','grn','invoice','opening_balance');
CREATE TYPE file_format       AS ENUM ('xml','csv','pdf','zip');
CREATE TYPE file_status       AS ENUM ('staged','queued','parsing','parsed','partial','failed','needs_password','removed');
CREATE TYPE mismatch_state    AS ENUM ('none','suggested','accepted','kept');
CREATE TYPE po_status         AS ENUM ('open','closed','cancelled');
CREATE TYPE line_type         AS ENUM ('goods','charge');
CREATE TYPE field_source      AS ENUM ('rule','ai','manual');
CREATE TYPE extraction_method AS ENUM ('xml_profile','csv_mapping','pdf_text','pdf_ocr','ai_fallback','ai_first','manual','shell');
CREATE TYPE warn_level        AS ENUM ('info','warning','error');

-- Vendors ----------------------------------------------------------------------
CREATE TYPE vendor_rule       AS ENUM ('gstin','pan','name_exact','alias','fuzzy','manual','none');
CREATE TYPE vendor_anchor     AS ENUM ('gstin','pan','name');
CREATE TYPE alias_source      AS ENUM ('merge','manual','learned','seed');
CREATE TYPE suggestion_state  AS ENUM ('pending','accepted','rejected');
CREATE TYPE ai_opinion        AS ENUM ('same','different','unsure');

-- Links ------------------------------------------------------------------------
CREATE TYPE link_type         AS ENUM ('inv_po','grn_po','inv_grn');
CREATE TYPE link_level        AS ENUM ('header','line');
CREATE TYPE link_method       AS ENUM ('labeled','reverse','via_grn','content','manual');   -- ladder steps 0,1,2,3,4
CREATE TYPE link_state        AS ENUM ('auto','suggested','confirmed','rejected');
CREATE TYPE resolution_outcome AS ENUM ('linked','suggested','unlinked');

-- Reconciliation ---------------------------------------------------------------
-- Declared in S7 precedence order: first match wins, and the enum sorts the same way.
CREATE TYPE case_status       AS ENUM ('duplicate','unlinked','needs_review','exception','pending','matched_flags','matched');
CREATE TYPE overlay_type      AS ENUM ('approved','on_hold','rejected');
CREATE TYPE action_type       AS ENUM ('confirm_link','change_link','approve','hold','reject','note','release');
CREATE TYPE reason_level      AS ENUM ('block','review','fail','pending','warn','info');
CREATE TYPE alignment_state   AS ENUM ('aligned','unsure','not_on_po','charge','unlinked');
CREATE TYPE run_status        AS ENUM ('queued','running','succeeded','failed');
CREATE TYPE run_trigger       AS ENUM ('user','auto_pending','manual_recon');

-- Export, AI, access, operations -----------------------------------------------
CREATE TYPE export_status     AS ENUM ('queued','building','ready','failed','expired');
CREATE TYPE ai_mode           AS ENUM ('rules_only','ai_assisted','ai_first');
CREATE TYPE ai_provider       AS ENUM ('claude','openai','gemini');
CREATE TYPE ai_key_storage    AS ENUM ('session','saved');
CREATE TYPE ai_hook           AS ENUM ('h1_extract','h2_item','h3_rerank','h4_vendor','h5_explain');
CREATE TYPE ai_outcome        AS ENUM ('ok','cache_hit','invalid_output','ungrounded','timeout','rate_limited','bad_key','budget_stopped','error');
CREATE TYPE user_role         AS ENUM ('ap_executive','manager','auditor');
CREATE TYPE scope_type        AS ENUM ('vendor','po','invoice');
CREATE TYPE processing_stage  AS ENUM ('received','sniffed','parsed','qa','gated','upserted','vendor_resolved','linked','ledger_updated','evaluated');
CREATE TYPE stage_outcome     AS ENUM ('ok','warn','error','skipped');
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
