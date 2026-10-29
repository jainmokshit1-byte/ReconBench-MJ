"""Provider capabilities (accuracy plan PD-1b, decision-log D103).

A provider card stores what the server-side Connect/Test found: whether the served model
reads a page image (``supports_vision``), whether it answers in a strict JSON schema
(``supports_json_schema``) and its context window in tokens (``context_window``, from
``GET /models``). NULL means not checked. Any change of server, model or key puts the
three back to NULL with the connection state. Additive only: three nullable columns, no
existing row changes. The downgrade drops them.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_ADD = r"""ALTER TABLE ai_provider_config
  ADD COLUMN supports_vision BOOLEAN,
  ADD COLUMN supports_json_schema BOOLEAN,
  ADD COLUMN context_window INTEGER,
  ADD CONSTRAINT ai_provider_config_context_window_ck
    CHECK (context_window IS NULL OR context_window > 0);"""

_REMOVE = r"""ALTER TABLE ai_provider_config
  DROP CONSTRAINT ai_provider_config_context_window_ck,
  DROP COLUMN context_window,
  DROP COLUMN supports_json_schema,
  DROP COLUMN supports_vision;"""


def upgrade() -> None:
    execute_sql_block(_ADD)


def downgrade() -> None:
    execute_sql_block(_REMOVE)
