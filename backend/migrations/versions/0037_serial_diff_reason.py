"""Reason code SERIAL_DIFF (serial numbers on invoice and GRN lines differ).

A warn-level reason: the invoice line and the goods-receipt line aligned to the same PO line
both carry serial numbers and the sets differ. The code joins every workspace's reason
catalogue with the seed text (``reconbench_app/db/seed_data.py``); a workspace that already
has it keeps its row. Additive only. The downgrade removes the code's catalogue rows.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_ADD = r"""INSERT INTO reason_code (
  id, workspace_id, code, level, stage, title, message, suggested_action,
  overridable, level_configurable, is_proposed, is_active
)
SELECT gen_random_uuid(), id, 'SERIAL_DIFF', 'warn'::reason_level, 'S4',
  'Serial numbers differ',
  'The serial numbers on the invoice line differ from those on the goods receipt.',
  'Compare the serial numbers on the invoice with the receipt.',
  TRUE, FALSE, FALSE, TRUE
FROM workspace
ON CONFLICT (workspace_id, code) DO NOTHING;"""

_REMOVE = r"""DELETE FROM reason_code WHERE code = 'SERIAL_DIFF';"""


def upgrade() -> None:
    execute_sql_block(_ADD)


def downgrade() -> None:
    execute_sql_block(_REMOVE)
