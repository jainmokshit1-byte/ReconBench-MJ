"""Last-manager lock for ``ai.manage`` (accuracy plan WI-PV3, decision O-18, decision-log D90).

``ai.manage`` is now enforced from the stored role: an active user whose
``app_user.role`` is ``manager``. This trigger keeps at least one active manager in every
workspace that has one: an UPDATE that changes ``role``, ``is_active`` or ``workspace_id``,
or a DELETE, raises ``check_violation`` (message ``last_manager``) when the transaction
would commit with the workspace left without an active manager. It is a deferred
constraint trigger, checked at COMMIT on the final state: a multi-row demotion of every
manager is refused, swapping managers inside one transaction is allowed, and removing a
whole workspace (its users, then the workspace row, in one transaction) is allowed. A
per-workspace advisory lock taken at the check serialises concurrent changes, so two
demotions committed at the same time cannot both pass. The bootstrap user is seeded as
``manager`` (``db/seed.py``), so a fresh workspace starts with one. A workspace with no
manager at all (none was ever stored) is not blocked.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

# ruff: noqa: E501 -- keep each DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_GUARD = r"""CREATE FUNCTION app_user_last_manager_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.role = 'manager' AND OLD.is_active
     AND (TG_OP = 'DELETE' OR NEW.role <> 'manager' OR NOT NEW.is_active
          OR NEW.workspace_id <> OLD.workspace_id) THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('reconbench.last_manager:' || OLD.workspace_id::text, 0));
    IF NOT EXISTS (SELECT 1 FROM workspace WHERE id = OLD.workspace_id) THEN
      RETURN NULL;  -- the whole workspace was removed in this transaction
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM app_user
       WHERE workspace_id = OLD.workspace_id
         AND role = 'manager'
         AND is_active
    ) THEN
      RAISE EXCEPTION 'last_manager: a workspace must keep at least one active manager'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER app_user_last_manager
  AFTER UPDATE OF role, is_active, workspace_id OR DELETE ON app_user
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_user_last_manager_guard();"""

_DROP = r"""DROP TRIGGER IF EXISTS app_user_last_manager ON app_user;
DROP FUNCTION IF EXISTS app_user_last_manager_guard();"""


def upgrade() -> None:
    execute_sql_block(_GUARD)


def downgrade() -> None:
    execute_sql_block(_DROP)
