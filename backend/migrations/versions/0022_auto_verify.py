"""Per-field D42 opt-in and the applied verified-version link (accuracy plan AC-6c).

* ``ai_setting.auto_verify_field_types`` is an explicit workspace opt-in, empty
  by default, limited to the field types whose benchmark gate passed.
* ``consensus_run.result_version_id`` points to the new derived document version
  when a consensus run applied verification state.

Runs as reconbench_migrator (revision 0015), so the existing table privileges
and default privileges apply.
"""

# ruff: noqa: E501 -- keep each validated DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_FIELDS = r"""ALTER TABLE ai_setting
  ADD COLUMN auto_verify_field_types TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE ai_setting ADD CONSTRAINT ai_setting_auto_verify_field_types_ck CHECK (
  auto_verify_field_types <@ ARRAY['doc_date', 'doc_no', 'gstin', 'line_amount', 'reference']::text[]
  AND cardinality(auto_verify_field_types) <= 5
);"""

_RESULT_VERSION = r"""ALTER TABLE consensus_run
  ADD COLUMN result_version_id UUID REFERENCES document_version(id);"""


def upgrade() -> None:
    execute_sql_block(_FIELDS)
    execute_sql_block(_RESULT_VERSION)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
