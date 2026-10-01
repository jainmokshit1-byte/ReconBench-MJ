"""4.11 Operations migration, sourced from Backend Schema §4.11."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0012"
down_revision = '0011'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 1505
    r"""CREATE TABLE dirty_scope (
  id               UUID PRIMARY KEY,
  workspace_id     UUID NOT NULL REFERENCES workspace(id),
  scope_kind       scope_type NOT NULL,
  scope_id         UUID NOT NULL,
  reason           TEXT NOT NULL,              -- e.g. 'document_changed', 'vendor_merged', 'link_changed', 'reject'
  mark_count       INTEGER NOT NULL DEFAULT 1,
  first_marked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_marked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at       TIMESTAMPTZ,
  processed_at     TIMESTAMPTZ
);
CREATE UNIQUE INDEX dirty_scope_open_uq ON dirty_scope (workspace_id, scope_kind, scope_id) WHERE processed_at IS NULL;
CREATE INDEX        dirty_scope_work_ix ON dirty_scope (workspace_id, first_marked_at)      WHERE processed_at IS NULL;
""",

    # Backend Schema source line 1536
    r"""CREATE TABLE processing_event (
  id                   UUID PRIMARY KEY,
  workspace_id         UUID NOT NULL REFERENCES workspace(id),
  source_file_id       UUID REFERENCES source_file(id),
  document_id          UUID REFERENCES document(id),
  document_version_id  UUID REFERENCES document_version(id),
  stage                processing_stage NOT NULL,
  outcome              stage_outcome NOT NULL,
  duration_ms          INTEGER,
  parser_version       TEXT,
  warning_codes        TEXT[] NOT NULL DEFAULT '{}',
  error_code           TEXT,
  job_id               BIGINT,
  detail               JSONB,
  occurred_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT processing_event_target_ck CHECK (source_file_id IS NOT NULL OR document_id IS NOT NULL)
);
CREATE INDEX processing_event_doc_ix  ON processing_event (document_id, occurred_at);
CREATE INDEX processing_event_file_ix ON processing_event (source_file_id, occurred_at);
CREATE INDEX processing_event_err_ix  ON processing_event (workspace_id, occurred_at DESC) WHERE outcome = 'error';
""",

    # Backend Schema source line 1562
    r"""CREATE TABLE idempotency_key (
  id               UUID PRIMARY KEY,
  workspace_id     UUID NOT NULL REFERENCES workspace(id),
  user_id          UUID NOT NULL REFERENCES app_user(id),
  idem_key         TEXT NOT NULL,
  endpoint         TEXT NOT NULL,               -- method and route template, e.g. 'POST /runs'
  request_hash     d_hash NOT NULL,             -- the same key with a different body is rejected
  response_status  INTEGER,                     -- NULL while the first request is still running
  response_body    JSONB,
  resource_type    TEXT,
  resource_id      UUID,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at       TIMESTAMPTZ NOT NULL,        -- created_at + 24 h
  UNIQUE (workspace_id, user_id, endpoint, idem_key)
);
CREATE INDEX idempotency_key_expiry_ix ON idempotency_key (expires_at);
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
