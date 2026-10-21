"""Persist exact document identity evidence for AC-8b re-scan grouping.

The table stores source SHA-256, ordered 64-bit page dHashes and the content
fingerprint for each parser version. It contains no page image bytes or text.
"""

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_IDENTITY = r"""CREATE TABLE document_identity (
  document_version_id UUID PRIMARY KEY REFERENCES document_version(id),
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  source_sha256       d_hash NOT NULL,
  source_locator      TEXT,
  page_hashes         TEXT[] NOT NULL DEFAULT '{}',
  content_fingerprint d_hash,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT document_identity_page_hashes_ck CHECK (cardinality(page_hashes) <= 100)
);
INSERT INTO document_identity (
  document_version_id, workspace_id, source_sha256, source_locator
)
SELECT version.id, version.workspace_id, source.sha256, version.locator ->> 'source'
FROM document_version AS version
JOIN source_file AS source
  ON source.id = version.source_file_id
 AND source.workspace_id = version.workspace_id;
CREATE INDEX document_identity_fingerprint_ix
  ON document_identity (workspace_id, content_fingerprint)
  WHERE content_fingerprint IS NOT NULL;
CREATE INDEX document_identity_source_ix
  ON document_identity (workspace_id, source_sha256, source_locator);
CREATE TRIGGER document_identity_append_only
  BEFORE UPDATE OR DELETE ON document_identity
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER document_identity_append_only_truncate
  BEFORE TRUNCATE ON document_identity
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON document_identity FROM reconbench_app;
REVOKE ALL ON document_identity FROM reconbench_report;"""


def upgrade() -> None:
    execute_sql_block(_IDENTITY)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
