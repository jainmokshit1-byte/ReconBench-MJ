"""Persist field origin and verification independently for matching v2."""

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_PROVENANCE = r"""ALTER TABLE field_evidence
  ALTER COLUMN confidence DROP NOT NULL,
  ADD COLUMN origin TEXT,
  ADD COLUMN verification TEXT NOT NULL DEFAULT 'none',
  ADD COLUMN verified_by TEXT,
  ADD COLUMN verified_at TIMESTAMPTZ;

ALTER TABLE field_evidence DISABLE TRIGGER field_evidence_append_only;

WITH evidence_owners AS (
  SELECT evidence.id, version.extraction_method, version.created_by
  FROM field_evidence AS evidence
  JOIN document_version AS version
    ON version.id = evidence.document_version_id
  WHERE evidence.document_version_id IS NOT NULL
  UNION ALL
  SELECT evidence.id, version.extraction_method, version.created_by
  FROM field_evidence AS evidence
  JOIN doc_line AS line ON line.id = evidence.doc_line_id
  JOIN document_version AS version ON version.id = line.document_version_id
  WHERE evidence.doc_line_id IS NOT NULL
)
UPDATE field_evidence AS evidence
SET origin = CASE
      WHEN evidence.source = 'manual' THEN 'manual'
      WHEN owners.extraction_method IN ('xml_profile', 'csv_mapping') THEN 'erp_import'
      WHEN owners.extraction_method IN ('pdf_text', 'pdf_ocr', 'ai_fallback', 'ai_first') THEN 'ocr'
      ELSE NULL
    END,
    verification = CASE WHEN evidence.source = 'manual' THEN 'user' ELSE 'none' END,
    verified_by = CASE
      WHEN evidence.source = 'manual' THEN COALESCE(owners.created_by::text, 'legacy_manual')
      ELSE NULL
    END,
    verified_at = CASE WHEN evidence.source = 'manual' THEN evidence.created_at ELSE NULL END,
    confidence = CASE WHEN evidence.source = 'manual' THEN NULL ELSE evidence.confidence END
FROM evidence_owners AS owners
WHERE evidence.id = owners.id;

ALTER TABLE field_evidence ENABLE TRIGGER field_evidence_append_only;

ALTER TABLE field_evidence
  ADD CONSTRAINT field_evidence_origin_ck
    CHECK (origin IS NULL OR origin IN ('ocr', 'manual', 'erp_import')),
  ADD CONSTRAINT field_evidence_verification_ck
    CHECK (verification IN ('none', 'user', 'system_check'));

CREATE INDEX field_evidence_verification_ix
  ON field_evidence (workspace_id, document_version_id, field_name, created_at, id)
  WHERE verification <> 'none';
CREATE INDEX field_evidence_line_verification_ix
  ON field_evidence (workspace_id, doc_line_id, field_name, created_at, id)
  WHERE verification <> 'none';"""


def upgrade() -> None:
    execute_sql_block(_PROVENANCE)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
