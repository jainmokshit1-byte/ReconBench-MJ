"""4.7 Reconciliation migration, sourced from Backend Schema §4.7."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0008"
down_revision = '0007'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 990
    r"""CREATE TABLE recon_run (
  id                  UUID PRIMARY KEY,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  status              run_status NOT NULL DEFAULT 'queued',
  trigger_kind        run_trigger NOT NULL DEFAULT 'user',
  scope               JSONB NOT NULL,                       -- §5.7
  scope_hash          d_hash NOT NULL,                      -- job idempotency key = run id + scope
  run_date            DATE NOT NULL,
  engine_version      TEXT NOT NULL,
  rules_hash          d_hash NOT NULL,
  config_version_id   UUID NOT NULL REFERENCES config_version(id),
  manifest_hash       d_hash,                               -- set once run_manifest is written
  replay_of_run_id    UUID REFERENCES recon_run(id),        -- P1 replay tooling
  ai_mode             ai_mode NOT NULL DEFAULT 'rules_only',
  ai_provider         ai_provider,
  ai_model            TEXT,
  ai_prompt_versions  JSONB,
  ai_consent          BOOLEAN NOT NULL DEFAULT false,       -- stored with the run (TR-AI-7)
  total_cases         INTEGER NOT NULL DEFAULT 0,
  done_cases          INTEGER NOT NULL DEFAULT 0,
  status_counts       JSONB NOT NULL DEFAULT '{}',          -- live counts by status for the progress bar
  metrics             JSONB NOT NULL DEFAULT '{}',          -- stage durations, straight-through rate, link-method mix, AI cost (TR-OPS-4)
  error_code          TEXT,
  error_message       TEXT,
  job_id              BIGINT,                               -- queue job; no foreign key on purpose (§4.11)
  requested_by        UUID REFERENCES app_user(id),         -- NULL for automatic Pending re-evaluation
  queued_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at          TIMESTAMPTZ,
  finished_at         TIMESTAMPTZ,
  version             INTEGER NOT NULL DEFAULT 1,
  CONSTRAINT recon_run_ai_consent_ck CHECK (ai_mode = 'rules_only' OR ai_consent),
  CONSTRAINT recon_run_progress_ck   CHECK (done_cases <= total_cases)
);
CREATE INDEX recon_run_recent_ix ON recon_run (workspace_id, queued_at DESC);
CREATE INDEX recon_run_active_ix ON recon_run (workspace_id) WHERE status IN ('queued','running');
""",

    # Backend Schema source line 1031
    r"""CREATE TABLE run_manifest (
  id             UUID PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES workspace(id),
  run_id         UUID NOT NULL UNIQUE REFERENCES recon_run(id),
  manifest_hash  d_hash NOT NULL,
  item_count     INTEGER NOT NULL,
  items          JSONB NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
""",

    # Backend Schema source line 1048
    r"""CREATE TABLE recon_case (
  id                  UUID PRIMARY KEY,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  run_id              UUID NOT NULL REFERENCES recon_run(id),
  invoice_id          UUID NOT NULL REFERENCES document(id),
  invoice_version_id  UUID NOT NULL REFERENCES document_version(id),
  vendor_id           UUID REFERENCES vendor(id),           -- as resolved at run time; NULL if unassigned
  status              case_status NOT NULL,                 -- computed status; overlays live in case_action
  aged                BOOLEAN NOT NULL DEFAULT false,       -- Pending older than the ageing limit; code stays GRN_PENDING
  pending_days        INTEGER,                              -- run_date − invoice date, for the "12 days" label
  duplicate_of_id     UUID REFERENCES document(id),         -- the original, when status = duplicate

  invoice_taxable     d_amount,
  invoice_total       d_amount,
  expected_taxable    d_amount,                             -- Σ billed qty × PO rate + approved charges
  variance            d_amount,
  variance_pct        NUMERIC(9,4),
  payable_taxable     d_amount NOT NULL DEFAULT 0,
  at_risk             d_amount NOT NULL DEFAULT 0,          -- 0 for duplicate and unlinked cases
  at_risk_gst         d_amount NOT NULL DEFAULT 0,          -- shown beside At-risk, not inside it (OQ-4)
  blocked_value       d_amount NOT NULL DEFAULT 0,          -- taxable value of duplicate and unlinked cases (OQ-6)
  payment_due_date    DATE,                                 -- invoice date + PO payment terms

  po_nos              TEXT[] NOT NULL DEFAULT '{}',         -- display copies for the list and the export
  grn_nos             TEXT[] NOT NULL DEFAULT '{}',
  link_summary        JSONB NOT NULL DEFAULT '[]',          -- §5.6
  reason_codes        TEXT[] NOT NULL DEFAULT '{}',
  checks              JSONB NOT NULL DEFAULT '[]',          -- header-level CheckResults, §5.5
  inputs              JSONB NOT NULL,                       -- version ids used, §5.6
  input_fingerprint   d_hash NOT NULL,                      -- stored on overrides for staleness (TR-REV-7)
  vendor_seq          BIGINT,
  po_seqs             JSONB NOT NULL DEFAULT '{}',          -- {"<po_document_id>": input_seq}
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, invoice_id),
  CONSTRAINT recon_case_aged_ck     CHECK (NOT aged OR status = 'exception'),
  CONSTRAINT recon_case_atrisk_ck   CHECK (status NOT IN ('duplicate','unlinked') OR at_risk = 0),
  CONSTRAINT recon_case_blocked_ck  CHECK (status IN ('duplicate','unlinked') OR blocked_value = 0)
);
CREATE INDEX recon_case_run_status_ix ON recon_case (run_id, status);
CREATE INDEX recon_case_reason_gin    ON recon_case USING gin (reason_codes);
CREATE INDEX recon_case_invoice_ix    ON recon_case (invoice_id, created_at DESC);
CREATE INDEX recon_case_vendor_ix     ON recon_case (workspace_id, vendor_id, status);
""",

    # Backend Schema source line 1098
    r"""CREATE TABLE recon_line_result (
  id                        UUID PRIMARY KEY,
  workspace_id              UUID NOT NULL REFERENCES workspace(id),
  run_id                    UUID NOT NULL REFERENCES recon_run(id),
  case_id                   UUID NOT NULL REFERENCES recon_case(id),
  invoice_line_id           UUID NOT NULL REFERENCES doc_line(id),
  line_no                   INTEGER NOT NULL,
  line_type                 line_type NOT NULL,
  status                    case_status NOT NULL,             -- this line's own status; the case takes the worst
  -- alignment (S3): never uses quantity or rate
  po_document_id            UUID REFERENCES document(id),
  po_line_no                INTEGER,
  po_doc_line_id            UUID REFERENCES doc_line(id),
  alignment_score           d_score,
  alignment_state           alignment_state NOT NULL,
  ai_item_opinion           ai_opinion,                       -- hook H2, advisory only
  ai_item_reason            TEXT,
  grn_document_ids          UUID[] NOT NULL DEFAULT '{}',
  uom                       TEXT,
  -- ledger row (S4)
  po_qty                    d_qty,
  po_cap                    d_qty,                            -- ordered × (1 + over-tolerance)
  billed_before             d_qty,
  billed_qty                d_qty,
  grn_accepted_cum          d_qty,
  grn_rejected_cum          d_qty,
  eligible_qty              d_qty,
  balance_after             d_qty,                            -- ordered − billed after; negative = over-billed (UI/UX Brief B6)
  shortfall_qty             d_qty,                            -- billed after − GRN cumulative, when positive
  provisional_before_count  INTEGER NOT NULL DEFAULT 0,       -- invoices under review inside "billed before"
  -- price and amount (S5)
  po_rate                   d_qty,
  invoice_net_rate          d_qty,
  rate_var_pct              NUMERIC(9,4),
  payable_rate              d_qty,
  line_taxable              d_amount,
  payable_taxable           d_amount,
  at_risk                   d_amount,
  reason_codes              TEXT[] NOT NULL DEFAULT '{}',
  checks                    JSONB NOT NULL DEFAULT '[]',      -- §5.5
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (case_id, line_no)
);
CREATE INDEX recon_line_result_run_ix ON recon_line_result (run_id);
CREATE INDEX recon_line_result_po_ix  ON recon_line_result (po_document_id, po_line_no);
CREATE INDEX recon_line_result_reason_gin ON recon_line_result USING gin (reason_codes);
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
