"""AI field origin (accuracy plan PD-2a, decision-log D103).

``field_evidence.origin`` may now be ``ai``: a value read by an AI provider. The reference
system check never accepts that origin, so an AI-read value is never system-checked and
counts only after a person verifies it. Only the check constraint changes: no data
changes, no backfill, no trigger change (live had no AI-read version when this was
written). The downgrade restores the previous check; it fails if any ``ai`` row exists.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_AI_ORIGIN = r"""ALTER TABLE field_evidence
  DROP CONSTRAINT field_evidence_origin_ck,
  ADD CONSTRAINT field_evidence_origin_ck
    CHECK (origin IS NULL OR origin IN ('ocr', 'manual', 'erp_import', 'ai'));"""

_PREVIOUS = r"""ALTER TABLE field_evidence
  DROP CONSTRAINT field_evidence_origin_ck,
  ADD CONSTRAINT field_evidence_origin_ck
    CHECK (origin IS NULL OR origin IN ('ocr', 'manual', 'erp_import'));"""


def upgrade() -> None:
    execute_sql_block(_AI_ORIGIN)


def downgrade() -> None:
    execute_sql_block(_PREVIOUS)
