"""Separate AI providers: Gemini and a company vLLM server (LLM plan WI-BE3, decision-log D65).

* ``ai_provider`` gains ``vllm``. ``ALTER TYPE ... ADD VALUE`` runs in its own
  autocommit block so later statements (and later transactions) may use it.
* ``ai_reader_strategy`` (``off``/``shadow``/``single``) and ``ai_apply_mode``
  (``suggest``/``merge``). There is no ``dual_agree``: providers are separate
  alternatives (owner decision of 16 Oct 2026, plan §0.6).
* ``ai_provider_config``: one row per workspace and provider. ``base_url`` only
  for ``vllm``; ``key_secret_id`` only for ``vllm`` (the Gemini key stays on
  ``ai_setting`` so one secret is never referenced twice).
* ``ai_setting`` gains ``reader_strategy``, ``primary_reader`` and ``apply_mode``
  (``off`` exactly when there is no primary reader).
* Backfill: every Gemini workspace gets a Gemini config row and
  ``reader_strategy = 'single', primary_reader = 'gemini'`` when page images were
  on, else ``'off'``. ``vision_enabled`` stays for one release (old code paths);
  the settings service keeps it equal to ``reader_strategy = 'single'``.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

# ruff: noqa: E501 -- keep each DDL statement readable as one block.

from __future__ import annotations

from alembic import op
from reconbench_app.db.migration_sql import execute_sql_block

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_PROVIDER_VALUE = r"""ALTER TYPE ai_provider ADD VALUE IF NOT EXISTS 'vllm';"""

_TYPES = r"""CREATE TYPE ai_reader_strategy AS ENUM ('off', 'shadow', 'single');
CREATE TYPE ai_apply_mode AS ENUM ('suggest', 'merge');"""

_CONFIG = r"""CREATE TABLE ai_provider_config (
  id              UUID NOT NULL,
  workspace_id    UUID NOT NULL REFERENCES workspace(id),
  provider        ai_provider NOT NULL,
  enabled         BOOLEAN NOT NULL DEFAULT false,
  model           TEXT,
  base_url        TEXT,
  key_secret_id   UUID REFERENCES secret(id),
  limits          JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_test_at    TIMESTAMPTZ,
  last_test_ok    BOOLEAN,
  last_health_at  TIMESTAMPTZ,
  last_health_ok  BOOLEAN,
  version         INTEGER NOT NULL DEFAULT 1,
  updated_by      UUID REFERENCES app_user(id),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ai_provider_config_pkey PRIMARY KEY (id),
  CONSTRAINT ai_provider_config_workspace_provider_key UNIQUE (workspace_id, provider),
  CONSTRAINT ai_provider_config_base_url_ck CHECK (provider = 'vllm' OR base_url IS NULL),
  CONSTRAINT ai_provider_config_key_ck CHECK (provider = 'vllm' OR key_secret_id IS NULL),
  CONSTRAINT ai_provider_config_model_ck CHECK (model IS NULL OR length(model) BETWEEN 1 AND 128),
  CONSTRAINT ai_provider_config_base_url_len_ck CHECK (base_url IS NULL OR length(base_url) BETWEEN 1 AND 512),
  CONSTRAINT ai_provider_config_limits_ck CHECK (jsonb_typeof(limits) = 'object'),
  CONSTRAINT ai_provider_config_version_ck CHECK (version >= 1)
);
REVOKE ALL ON ai_provider_config FROM reconbench_report;"""

_SETTING = r"""ALTER TABLE ai_setting
  ADD COLUMN reader_strategy ai_reader_strategy NOT NULL DEFAULT 'off',
  ADD COLUMN primary_reader ai_provider,
  ADD COLUMN apply_mode ai_apply_mode NOT NULL DEFAULT 'suggest';

INSERT INTO ai_provider_config (id, workspace_id, provider, enabled, model, updated_by, updated_at)
SELECT gen_random_uuid(), workspace_id, 'gemini', mode <> 'rules_only', model, updated_by, updated_at
  FROM ai_setting
 WHERE provider = 'gemini';

UPDATE ai_setting
   SET reader_strategy = CASE WHEN vision_enabled THEN 'single'::ai_reader_strategy ELSE 'off'::ai_reader_strategy END,
       primary_reader = CASE WHEN vision_enabled THEN 'gemini'::ai_provider END
 WHERE provider = 'gemini';

ALTER TABLE ai_setting
  ADD CONSTRAINT ai_setting_reader_strategy_ck CHECK (
    (reader_strategy = 'off') = (primary_reader IS NULL)
  );"""


def upgrade() -> None:
    with op.get_context().autocommit_block():
        execute_sql_block(_PROVIDER_VALUE)
    execute_sql_block(_TYPES)
    execute_sql_block(_CONFIG)
    execute_sql_block(_SETTING)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
