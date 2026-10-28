"""Reason code AI_VALUE_UNVERIFIED (accuracy plan PD-2b, decision-log D103).

A case whose invoice holds a value read by AI that no person has verified needs review
with this reason. The code joins every workspace's reason catalogue with the seed text
(``reconbench_app/db/seed_data.py``); a workspace that already has it keeps its row.
Additive only: no existing row changes. The downgrade removes the code's catalogue rows.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_ADD = r"""INSERT INTO reason_code (
  id, workspace_id, code, level, stage, title, message, suggested_action,
  overridable, level_configurable, is_proposed, is_active
)
SELECT gen_random_uuid(), id, 'AI_VALUE_UNVERIFIED', 'review'::reason_level, 'S1',
  'AI-read values not verified',
  'A value read by AI counts only after a person verifies it.',
  'Open the document and verify each AI-read value against the page.',
  TRUE, FALSE, FALSE, TRUE
FROM workspace
ON CONFLICT (workspace_id, code) DO NOTHING;"""

_REMOVE = r"""DELETE FROM reason_code WHERE code = 'AI_VALUE_UNVERIFIED';"""


def upgrade() -> None:
    execute_sql_block(_ADD)


def downgrade() -> None:
    execute_sql_block(_REMOVE)
