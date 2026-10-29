"""Scanned-invoice switch and the reading method per version (accuracy plan PD-2c, D103).

``ai_setting.scanned_invoice_reader`` is the one workspace switch "Scanned invoices: read
with rules / read with AI" (decision 2), default ``rules``. ``document_version.reading_method``
is the method that actually read a version (``rules`` or ``ai``). A person's save copies it
from the version it replaces, so it survives a correction (F9). A check ties it to
``extraction_method``: an AI method is always ``ai``, a parser method always ``rules``, and
only a person's version (``manual``) carries either. ``document_text.scanned`` keeps the
per-page scan check, so "scanned" never means "OCR was used": an OCR assist on a digital page
is still digital (D103 decision 1).

Additive: three columns with defaults and two checks. The only rows the backfill touches are
versions already read by AI (``ai_first``/``ai_fallback``), which become ``ai``, with the
append-only trigger paused for that one statement as in revision 0024; every other row keeps
the default. Existing pages stay ``scanned = false``, so no earlier upload is read by AI. The
downgrade drops the columns.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_ADD = r"""ALTER TABLE ai_setting
  ADD COLUMN scanned_invoice_reader TEXT NOT NULL DEFAULT 'rules',
  ADD CONSTRAINT ai_setting_scanned_invoice_reader_ck
    CHECK (scanned_invoice_reader IN ('rules', 'ai'));
ALTER TABLE document_version
  ADD COLUMN reading_method TEXT NOT NULL DEFAULT 'rules';
ALTER TABLE document_version DISABLE TRIGGER document_version_append_only;
UPDATE document_version SET reading_method = 'ai'
  WHERE extraction_method IN ('ai_first', 'ai_fallback');
ALTER TABLE document_version ENABLE TRIGGER document_version_append_only;
ALTER TABLE document_version
  ADD CONSTRAINT document_version_reading_method_ck
    CHECK (
      reading_method IN ('rules', 'ai')
      AND (
        extraction_method = 'manual'
        OR (extraction_method IN ('ai_first', 'ai_fallback')) = (reading_method = 'ai')
      )
    );
ALTER TABLE document_text
  ADD COLUMN scanned BOOLEAN NOT NULL DEFAULT false;"""

_REMOVE = r"""ALTER TABLE document_text
  DROP COLUMN scanned;
ALTER TABLE document_version
  DROP CONSTRAINT document_version_reading_method_ck,
  DROP COLUMN reading_method;
ALTER TABLE ai_setting
  DROP CONSTRAINT ai_setting_scanned_invoice_reader_ck,
  DROP COLUMN scanned_invoice_reader;"""


def upgrade() -> None:
    execute_sql_block(_ADD)


def downgrade() -> None:
    execute_sql_block(_REMOVE)
