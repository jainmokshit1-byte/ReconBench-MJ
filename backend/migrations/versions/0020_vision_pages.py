"""Vision reader (accuracy plan AC-5, decision-log D37): hook, workspace toggle, page images.

* ``ai_hook`` gains ``h6_vision`` (masked page images read by the vision reader).
* ``ai_setting.vision_enabled``: the workspace opted into page images in the AI
  consent flow; off by default, so existing settings and consent are unchanged.
* ``document_page_image``: one row per page of a parsed document version that
  was prepared for the vision reader. The masked image bytes are transient (the
  AI worker clears them once no vision request for the version is live); the
  hash, render geometry and mask boxes stay as evidence for later phases.
  Reporting users never see this table.

Runs as reconbench_migrator (revision 0015), so default privileges apply. The
new enum value is not used inside this transaction.
"""

# ruff: noqa: E501 -- keep each DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_HOOK = r"""ALTER TYPE ai_hook ADD VALUE IF NOT EXISTS 'h6_vision';"""

_SETTING = r"""ALTER TABLE ai_setting ADD COLUMN vision_enabled boolean NOT NULL DEFAULT false;"""

_PAGE_IMAGE = r"""CREATE TABLE document_page_image (
  document_version_id UUID NOT NULL REFERENCES document_version(id),
  page                INTEGER NOT NULL,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  image_sha256        d_hash NOT NULL,
  mime_type           TEXT NOT NULL,
  width               INTEGER NOT NULL,
  height              INTEGER NOT NULL,
  byte_size           INTEGER NOT NULL,
  render              JSONB NOT NULL,
  mask                JSONB NOT NULL,
  image               BYTEA,
  purged_at           TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT document_page_image_pkey PRIMARY KEY (document_version_id, page),
  CONSTRAINT document_page_image_page_ck CHECK (page >= 1),
  CONSTRAINT document_page_image_mime_ck CHECK (mime_type IN ('image/jpeg', 'image/png')),
  CONSTRAINT document_page_image_size_ck CHECK (width > 0 AND height > 0 AND byte_size > 0),
  CONSTRAINT document_page_image_purge_ck CHECK ((image IS NULL) = (purged_at IS NOT NULL)),
  CONSTRAINT document_page_image_bytes_ck CHECK (image IS NULL OR octet_length(image) = byte_size)
);
CREATE INDEX document_page_image_live_ix ON document_page_image (workspace_id, document_version_id)
  WHERE image IS NOT NULL;
REVOKE ALL ON document_page_image FROM reconbench_report;"""


def upgrade() -> None:
    execute_sql_block(_HOOK)
    execute_sql_block(_SETTING)
    execute_sql_block(_PAGE_IMAGE)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
