"""Persist worker maintenance state so new processes remain parked."""

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execute_sql_block(
        r"""CREATE TABLE worker_control (
  control_id SMALLINT PRIMARY KEY DEFAULT 1,
  paused BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT worker_control_singleton_ck CHECK (control_id = 1)
);"""
    )
    execute_sql_block(
        "INSERT INTO worker_control (control_id, paused) VALUES (1, FALSE);"
    )


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
