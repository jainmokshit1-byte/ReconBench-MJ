"""4.3 Ingestion migration, sourced from Backend Schema §4.3."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0004"
down_revision = '0003'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 415
    r"""CREATE TABLE batch (
  id                  UUID PRIMARY KEY,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  label               TEXT,                            -- optional at creation
  label_is_suggested  BOOLEAN NOT NULL DEFAULT false,  -- suggested from the month of the most common invoice date
  period_start        DATE,
  period_end          DATE,
  created_by          UUID REFERENCES app_user(id),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  version             INTEGER NOT NULL DEFAULT 1,
  CONSTRAINT batch_period_ck CHECK (period_start IS NULL OR period_end IS NULL OR period_end >= period_start)
);
CREATE INDEX batch_recent_ix ON batch (workspace_id, created_at DESC);
""",

    # Backend Schema source line 435
    r"""CREATE TABLE source_file (
  id                 UUID PRIMARY KEY,
  workspace_id       UUID NOT NULL REFERENCES workspace(id),
  batch_id           UUID REFERENCES batch(id),
  parent_file_id     UUID REFERENCES source_file(id),   -- ZIP entry → its archive
  sha256             d_hash NOT NULL,
  display_name       TEXT NOT NULL,
  archive_path       TEXT,                              -- path inside the ZIP; display only
  zone               upload_zone NOT NULL,                 -- effective zone; changes if a TYPE_MISMATCH is accepted
  zone_uploaded      upload_zone NOT NULL,                 -- the zone the user dropped it in
  format             file_format NOT NULL,              -- from content sniffing, not the extension
  sniffed_type       doc_type,
  mismatch_state     mismatch_state NOT NULL DEFAULT 'none',
  status             file_status NOT NULL DEFAULT 'staged',
  error_code         TEXT,
  error_message      TEXT,
  attempt_count      INTEGER NOT NULL DEFAULT 0,        -- infrastructure retries, at most 3
  size_bytes         BIGINT NOT NULL,
  page_count         INTEGER,
  object_key         TEXT NOT NULL,                     -- object-store key derived from sha256
  layout_signature   TEXT,
  parser_version     TEXT,
  docs_found         INTEGER NOT NULL DEFAULT 0,
  docs_new           INTEGER NOT NULL DEFAULT 0,
  docs_updated       INTEGER NOT NULL DEFAULT 0,
  docs_skipped       INTEGER NOT NULL DEFAULT 0,
  reupload_skips     INTEGER NOT NULL DEFAULT 0,        -- times identical bytes were uploaded again
  uploaded_by        UUID REFERENCES app_user(id),
  uploaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  queued_at          TIMESTAMPTZ,
  parse_started_at   TIMESTAMPTZ,
  parse_finished_at  TIMESTAMPTZ,
  removed_at         TIMESTAMPTZ,
  removed_by         UUID REFERENCES app_user(id),
  object_purged_at   TIMESTAMPTZ,                       -- set by the retention job
  version            INTEGER NOT NULL DEFAULT 1,
  CONSTRAINT source_file_removed_ck CHECK ((status = 'removed') = (removed_at IS NOT NULL))
);
CREATE UNIQUE INDEX source_file_sha_uq    ON source_file (workspace_id, sha256);
CREATE INDEX        source_file_batch_ix  ON source_file (workspace_id, batch_id, status);
CREATE INDEX        source_file_parent_ix ON source_file (parent_file_id) WHERE parent_file_id IS NOT NULL;
CREATE INDEX        source_file_status_ix ON source_file (workspace_id, status, uploaded_at DESC);
""",

    # Backend Schema source line 489
    r"""CREATE TABLE layout_profile (
  id            UUID PRIMARY KEY,
  workspace_id  UUID NOT NULL REFERENCES workspace(id),
  signature     TEXT NOT NULL,                        -- hash of the XML tag set / normalised CSV header set / PDF layout
  kind          TEXT NOT NULL CHECK (kind IN ('xml_profile','csv_mapping','pdf_labels')),
  vendor_id     UUID REFERENCES vendor(id),           -- set for per-vendor PDF label memory (P1)
  body          JSONB NOT NULL,
  created_by    UUID REFERENCES app_user(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  version       INTEGER NOT NULL DEFAULT 1,
  UNIQUE NULLS NOT DISTINCT (workspace_id, signature, kind, vendor_id)
);
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
