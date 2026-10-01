"""4.6 Configuration migration, sourced from Backend Schema §4.6."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0007"
down_revision = '0006'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 931
    r"""CREATE TABLE config_version (
  id                 UUID PRIMARY KEY,
  workspace_id       UUID NOT NULL REFERENCES workspace(id),
  seq                INTEGER NOT NULL,
  parent_version_id  UUID REFERENCES config_version(id),
  body               JSONB NOT NULL,                  -- {"engine": {...}, "app": {...}}
  engine_hash        d_hash NOT NULL,                 -- SHA-256 of canonical JSON of body.engine; part of rules_hash
  body_hash          d_hash NOT NULL,                 -- SHA-256 of canonical JSON of the whole body
  change_note        TEXT,
  created_by         UUID REFERENCES app_user(id),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, seq)
);
""",

    # Backend Schema source line 950
    r"""CREATE TABLE config_active (
  id                 UUID PRIMARY KEY,
  workspace_id       UUID NOT NULL REFERENCES workspace(id),
  config_version_id  UUID NOT NULL REFERENCES config_version(id),
  activated_by       UUID REFERENCES app_user(id),
  activated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  version            INTEGER NOT NULL DEFAULT 1,
  UNIQUE (workspace_id)
);
""",

    # Backend Schema source line 965
    r"""CREATE TABLE reason_code (
  id                  UUID PRIMARY KEY,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  code                TEXT NOT NULL,
  level               reason_level NOT NULL,
  stage               TEXT NOT NULL CHECK (stage IN ('S1','S2','S3','S4','S5','S6','S7')),
  title               TEXT NOT NULL,                   -- short plain-language chip text (UI/UX Brief §9.5)
  message             TEXT NOT NULL,
  suggested_action    TEXT NOT NULL,
  overridable         BOOLEAN NOT NULL DEFAULT true,
  level_configurable  BOOLEAN NOT NULL DEFAULT false,  -- DATE_ORDER: fail by default, configurable to warn
  is_proposed         BOOLEAN NOT NULL DEFAULT false,  -- OQ-8 additions awaiting approval
  is_active           BOOLEAN NOT NULL DEFAULT true,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, code),
  CONSTRAINT reason_code_fmt_ck CHECK (code ~ '^[A-Z][A-Z0-9_]+$')
);
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
