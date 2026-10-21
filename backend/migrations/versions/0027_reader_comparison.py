"""Shadow reader comparison store (LLM plan WI-BE6, decision-log D66).

* ``ai_request.shadow``: a vision request sent while the workspace runs its one
  reader (``primary_reader``) in ``shadow``. Only ``h6_vision`` requests may be
  shadow requests. Existing rows are not shadow (default ``false``).
* ``reader_comparison``: append-only, one row per applied shadow request. It
  keeps counts and per-field labels (``agree_ocr``, ``disagree_ocr``,
  ``llm_only``, ``ocr_only``, ``unreadable``), never a value. The report role
  cannot read it (as ``consensus_decision``); the application role may only
  insert and read.

There is no secondary reader and no LLM x LLM comparison: providers are separate
alternatives (owner decision of 16 Oct 2026, plan §0.6).

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

# ruff: noqa: E501 -- keep each DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_REQUEST = r"""ALTER TABLE ai_request
  ADD COLUMN shadow BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE ai_request
  ADD CONSTRAINT ai_request_shadow_ck CHECK (NOT shadow OR hook = 'h6_vision');"""

_COMPARISON = r"""CREATE TABLE reader_comparison (
  id                   UUID NOT NULL,
  workspace_id         UUID NOT NULL REFERENCES workspace(id),
  document_version_id  UUID NOT NULL REFERENCES document_version(id),
  ai_request_id        UUID NOT NULL REFERENCES ai_request(id),
  provider             ai_provider NOT NULL,
  model                TEXT NOT NULL,
  prompt_version       TEXT NOT NULL,
  schema_version       TEXT NOT NULL,
  reader_params_digest TEXT NOT NULL,
  counts               JSONB NOT NULL,
  labels               JSONB NOT NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT reader_comparison_pkey PRIMARY KEY (id),
  CONSTRAINT reader_comparison_request_key UNIQUE (workspace_id, ai_request_id),
  CONSTRAINT reader_comparison_text_len_ck CHECK (
    length(model) BETWEEN 1 AND 200 AND length(prompt_version) BETWEEN 1 AND 64
    AND length(schema_version) BETWEEN 1 AND 64
  ),
  CONSTRAINT reader_comparison_digest_ck CHECK (reader_params_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT reader_comparison_counts_ck CHECK (jsonb_typeof(counts) = 'object'),
  CONSTRAINT reader_comparison_labels_ck CHECK (jsonb_typeof(labels) = 'array')
);
CREATE INDEX reader_comparison_created_ix ON reader_comparison (workspace_id, created_at);
CREATE TRIGGER reader_comparison_append_only BEFORE UPDATE OR DELETE ON reader_comparison
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON reader_comparison FROM reconbench_app;
REVOKE ALL ON reader_comparison FROM reconbench_report;"""


def upgrade() -> None:
    execute_sql_block(_REQUEST)
    execute_sql_block(_COMPARISON)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
