"""4.9 Export migration, sourced from Backend Schema §4.9."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0010"
down_revision = '0009'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 1350
    r"""CREATE TABLE export_file (
  id                  UUID PRIMARY KEY,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  run_id              UUID NOT NULL REFERENCES recon_run(id),   -- anchor run; the job key is run id + filter hash
  included_run_ids    UUID[] NOT NULL DEFAULT '{}',             -- every run the exported cases came from
  filter              JSONB NOT NULL,                           -- §5.9
  filter_hash         d_hash NOT NULL,
  scope               TEXT NOT NULL CHECK (scope IN ('filtered','all')),
  as_of               TIMESTAMPTZ NOT NULL,                     -- when the current case pointers were read
  status              export_status NOT NULL DEFAULT 'queued',
  sheets              TEXT[] NOT NULL DEFAULT '{}',
  case_count          INTEGER,
  object_key          TEXT,
  size_bytes          BIGINT,
  error_code          TEXT,
  error_message       TEXT,
  job_id              BIGINT,
  requested_by        UUID NOT NULL REFERENCES app_user(id),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  built_at            TIMESTAMPTZ,
  expires_at          TIMESTAMPTZ,
  last_downloaded_at  TIMESTAMPTZ,
  download_count      INTEGER NOT NULL DEFAULT 0,
  version             INTEGER NOT NULL DEFAULT 1,
  CONSTRAINT export_ready_ck CHECK (status <> 'ready' OR (object_key IS NOT NULL AND expires_at IS NOT NULL))
);
CREATE INDEX export_file_run_ix    ON export_file (run_id, filter_hash);
CREATE INDEX export_file_expiry_ix ON export_file (expires_at) WHERE status = 'ready';
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
