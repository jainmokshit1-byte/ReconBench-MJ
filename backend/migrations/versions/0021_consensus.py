"""Dark per-field consensus runs and append-only decisions (accuracy plan AC-6b)."""

# ruff: noqa: E501 -- keep each validated DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_RUN = r"""CREATE TABLE consensus_run (
  id                  UUID NOT NULL,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  document_version_id UUID NOT NULL REFERENCES document_version(id),
  ai_request_id       UUID NOT NULL REFERENCES ai_request(id),
  ai_call_id          UUID NOT NULL REFERENCES ai_call_log(id),
  consensus_version   TEXT NOT NULL,
  status              TEXT NOT NULL DEFAULT 'queued',
  summary             JSONB,
  error_code          TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at         TIMESTAMPTZ,
  CONSTRAINT consensus_run_pkey PRIMARY KEY (id),
  CONSTRAINT consensus_run_workspace_request_key UNIQUE (workspace_id, ai_request_id),
  CONSTRAINT consensus_run_status_ck CHECK (status IN ('queued', 'succeeded', 'stale', 'failed')),
  CONSTRAINT consensus_run_finished_ck CHECK ((status = 'queued') = (finished_at IS NULL)),
  CONSTRAINT consensus_run_summary_ck CHECK ((status = 'succeeded') = (summary IS NOT NULL)),
  CONSTRAINT consensus_run_error_ck CHECK ((status = 'failed') = (error_code IS NOT NULL))
);
CREATE INDEX consensus_run_version_ix
  ON consensus_run (workspace_id, document_version_id, created_at DESC);
REVOKE ALL ON consensus_run FROM reconbench_report;"""

_DECISION = r"""CREATE TABLE consensus_decision (
  id                  UUID NOT NULL,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  consensus_run_id    UUID NOT NULL REFERENCES consensus_run(id),
  document_version_id UUID NOT NULL REFERENCES document_version(id),
  field_key           TEXT NOT NULL,
  scope               TEXT NOT NULL,
  line_no             INTEGER,
  field_name          TEXT NOT NULL,
  field_type          TEXT,
  trust_level         TEXT NOT NULL,
  decision_rule       TEXT NOT NULL,
  reason              TEXT NOT NULL,
  readings            JSONB NOT NULL,
  supporting          TEXT[] NOT NULL DEFAULT '{}',
  crop                JSONB,
  failed_validators   TEXT[] NOT NULL DEFAULT '{}',
  auto_verified       BOOLEAN NOT NULL DEFAULT false,
  reopened            BOOLEAN NOT NULL DEFAULT false,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT consensus_decision_pkey PRIMARY KEY (id),
  CONSTRAINT consensus_decision_run_field_key UNIQUE (workspace_id, consensus_run_id, field_key),
  CONSTRAINT consensus_decision_scope_ck CHECK (
    (scope = 'header' AND line_no IS NULL) OR (scope = 'line' AND line_no >= 1)
  ),
  CONSTRAINT consensus_decision_trust_level_ck CHECK (
    trust_level IN ('l0', 'l1', 'l2', 'l3', 'rejected')
  ),
  CONSTRAINT consensus_decision_rule_ck CHECK (
    decision_rule IN ('existing', 'd42', 'single', 'conflict', 'reopen', 'excluded', 'vision_only')
  ),
  CONSTRAINT consensus_decision_field_type_ck CHECK (
    field_type IS NULL OR field_type IN (
      'doc_no', 'doc_date', 'gstin', 'amount', 'reference',
      'line_amount', 'line_quantity', 'line_rate'
    )
  )
);
CREATE INDEX consensus_decision_run_ix ON consensus_decision (workspace_id, consensus_run_id);
CREATE TRIGGER consensus_decision_append_only BEFORE UPDATE OR DELETE ON consensus_decision
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON consensus_decision FROM reconbench_app;
REVOKE ALL ON consensus_decision FROM reconbench_report;"""


def upgrade() -> None:
    execute_sql_block(_RUN)
    execute_sql_block(_DECISION)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
