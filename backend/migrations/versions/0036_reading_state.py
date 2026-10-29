"""Waiting for reading (accuracy plan PD-2f, D103 Q3).

``document.reading_state`` says whether a document's current version may be used:
``ready`` (the default, every document read by rules), ``waiting`` (a scanned invoice the
AI is reading, switch on AI) or ``failed`` (the AI reading failed). Mapping, vendor
resolution and reconciliation use only ``ready`` documents, so nothing read by rules
becomes current for an AI-read invoice until a person picks Retry or "Read with rules".
``document.reading_failure_code`` keeps the stated reason of the last failure.

Additive: two columns with a default and one check; no existing row changes (all stay
``ready``). The downgrade drops the columns.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

_ADD = r"""ALTER TABLE document
  ADD COLUMN reading_state TEXT NOT NULL DEFAULT 'ready',
  ADD COLUMN reading_failure_code TEXT,
  ADD CONSTRAINT document_reading_state_ck
    CHECK (
      reading_state IN ('ready', 'waiting', 'failed')
      AND (reading_state = 'failed') = (reading_failure_code IS NOT NULL)
    );"""

_REMOVE = r"""ALTER TABLE document
  DROP CONSTRAINT document_reading_state_ck,
  DROP COLUMN reading_failure_code,
  DROP COLUMN reading_state;"""


def upgrade() -> None:
    execute_sql_block(_ADD)


def downgrade() -> None:
    execute_sql_block(_REMOVE)
