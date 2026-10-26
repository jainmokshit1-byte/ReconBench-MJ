"""Duplicate holds for uploaded invoices (accuracy plan PD-0b-4, decision-log D103 decision 9).

``duplicate_hold``: one row per document read from an upload that matches an active
invoice and was therefore not ingested. ``kind`` is ``duplicate`` (same vendor, invoice
number and financial year) or ``possible`` (an uncertain match: vendor not resolved,
number read at low confidence or read differently, or the re-scan signals). The document
is not created, mapped or reconciled while the hold is ``open``; a person decides
``discarded`` (default), ``replaced`` (duplicate: a new version of the original) or
``kept`` (possible: ingested as a new document). Additive only: no existing row changes.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

# ruff: noqa: E501 -- keep each DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_HOLD = r"""CREATE TABLE duplicate_hold (
  id                        UUID NOT NULL,
  workspace_id              UUID NOT NULL REFERENCES workspace(id),
  source_file_id            UUID NOT NULL REFERENCES source_file(id),
  source_locator            TEXT NOT NULL DEFAULT '',
  kind                      TEXT NOT NULL,
  reason                    TEXT NOT NULL,
  duplicate_of_document_id  UUID NOT NULL REFERENCES document(id),
  doc_no                    TEXT,
  state                     TEXT NOT NULL DEFAULT 'open',
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_by                UUID REFERENCES app_user(id),
  decided_at                TIMESTAMPTZ,
  version                   INTEGER NOT NULL DEFAULT 1,
  CONSTRAINT duplicate_hold_pkey PRIMARY KEY (id),
  CONSTRAINT duplicate_hold_document_key UNIQUE (workspace_id, source_file_id, source_locator),
  CONSTRAINT duplicate_hold_kind_ck CHECK (kind IN ('duplicate', 'possible')),
  CONSTRAINT duplicate_hold_state_ck CHECK (state IN ('open', 'discarded', 'replaced', 'kept')),
  CONSTRAINT duplicate_hold_decision_ck CHECK (
    (state = 'open') = (decided_at IS NULL)
    AND (state <> 'replaced' OR kind = 'duplicate')
    AND (state <> 'kept' OR kind = 'possible')
  ),
  CONSTRAINT duplicate_hold_text_len_ck CHECK (
    length(reason) BETWEEN 1 AND 64 AND length(source_locator) <= 512
    AND (doc_no IS NULL OR length(doc_no) <= 256)
  ),
  CONSTRAINT duplicate_hold_version_ck CHECK (version >= 1)
);
CREATE INDEX duplicate_hold_open_ix ON duplicate_hold (workspace_id, state, created_at);
CREATE INDEX duplicate_hold_original_ix ON duplicate_hold (workspace_id, duplicate_of_document_id);
REVOKE DELETE, TRUNCATE ON duplicate_hold FROM reconbench_app;"""


def upgrade() -> None:
    execute_sql_block(_HOLD)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
