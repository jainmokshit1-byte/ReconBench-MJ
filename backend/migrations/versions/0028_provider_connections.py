"""Provider connections, defaults and cost-cap fields (accuracy plan WI-PV1, decision-log D80/D81).

Data model only. No application code reads the new columns yet; the connect, test,
disconnect, rotate, selection and cost-cap behaviour arrive with WI-PV2 .. PV7.

* ``ai_provider_config`` (one row per workspace and provider) gains the connection
  state (``connected_at``, ``last_error_code``, ``last_error_at``, ``key_rotated_at``),
  the consent acceptance made at connect (``consent_fingerprint``, ``consent_accepted_by``,
  ``consent_accepted_at``), the Gemini cost-cap fields in rupees (``monthly_cost_cap_inr``,
  ``cap_warn_pct``, ``doc_cost_flag_inr``), the per-provider auto-verify list (starts
  empty), the per-provider ``apply_mode`` and the circuit-breaker state. The old rule that
  only a ``vllm`` card may reference a key is dropped so the Gemini key can move to its
  card (WI-PV2 performs the move; until then ``ai_setting.key_secret_id`` stays in use).
* ``ai_setting`` gains the workspace defaults ``extraction_provider`` (page images and
  H1) and ``assist_provider`` (H2 .. H4), a ``shadow_provider`` that may run beside the
  live provider, and ``allow_provider_fallback`` (off). ``reader_strategy`` and
  ``primary_reader`` stay readable for one release.
* ``batch.provider_override``: the provider chosen for one upload.
* ``ai_endpoint_allow``: append-only workspace allowlist log (``add``/``remove``); the
  current list is the latest action per URL. Entries only count inside the deployment
  ceiling (``RB_AI_VLLM_ENDPOINTS``), which WI-PV4 enforces.
* ``provider_measurement``: append-only, counts-only measurements per provider and
  field type for the Settings comparison note.

Backfill keeps today's behaviour: Gemini workspaces keep Gemini as extraction and assist
provider; a workspace that ran a reader in ``shadow`` gets that reader as
``shadow_provider``; every Gemini card gets the owner's cap values (2,500 / 80% / 20
rupees, decision D81); every per-provider auto-verify list starts empty.

Runs as reconbench_migrator (revision 0015), so default privileges apply.
"""

# ruff: noqa: E501 -- keep each DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_CONFIG = r"""ALTER TABLE ai_provider_config
  ADD COLUMN connected_at          TIMESTAMPTZ,
  ADD COLUMN last_error_code       TEXT,
  ADD COLUMN last_error_at         TIMESTAMPTZ,
  ADD COLUMN key_rotated_at        TIMESTAMPTZ,
  ADD COLUMN consent_fingerprint   TEXT,
  ADD COLUMN consent_accepted_by   UUID REFERENCES app_user(id),
  ADD COLUMN consent_accepted_at   TIMESTAMPTZ,
  ADD COLUMN monthly_cost_cap_inr  d_amount,
  ADD COLUMN cap_warn_pct          SMALLINT NOT NULL DEFAULT 80,
  ADD COLUMN doc_cost_flag_inr     d_amount,
  ADD COLUMN auto_verify_field_types TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN apply_mode            ai_apply_mode NOT NULL DEFAULT 'suggest',
  ADD COLUMN breaker_state         TEXT NOT NULL DEFAULT 'closed',
  ADD COLUMN breaker_changed_at    TIMESTAMPTZ;

ALTER TABLE ai_provider_config DROP CONSTRAINT ai_provider_config_key_ck;

ALTER TABLE ai_provider_config
  ADD CONSTRAINT ai_provider_config_error_code_ck CHECK (last_error_code IS NULL OR length(last_error_code) BETWEEN 1 AND 64),
  ADD CONSTRAINT ai_provider_config_consent_ck CHECK (
    (consent_fingerprint IS NULL) = (consent_accepted_at IS NULL)
    AND (consent_accepted_at IS NULL) = (consent_accepted_by IS NULL)
  ),
  ADD CONSTRAINT ai_provider_config_consent_len_ck CHECK (consent_fingerprint IS NULL OR length(consent_fingerprint) BETWEEN 1 AND 128),
  ADD CONSTRAINT ai_provider_config_cap_ck CHECK (monthly_cost_cap_inr IS NULL OR monthly_cost_cap_inr >= 0),
  ADD CONSTRAINT ai_provider_config_cap_warn_ck CHECK (cap_warn_pct BETWEEN 1 AND 100),
  ADD CONSTRAINT ai_provider_config_doc_flag_ck CHECK (doc_cost_flag_inr IS NULL OR doc_cost_flag_inr >= 0),
  ADD CONSTRAINT ai_provider_config_auto_verify_ck CHECK (
    auto_verify_field_types <@ ARRAY['doc_date', 'doc_no', 'gstin', 'line_amount', 'reference']::text[]
    AND cardinality(auto_verify_field_types) <= 5
  ),
  ADD CONSTRAINT ai_provider_config_breaker_ck CHECK (breaker_state IN ('closed', 'open'));

UPDATE ai_provider_config
   SET monthly_cost_cap_inr = 2500.00,
       cap_warn_pct = 80,
       doc_cost_flag_inr = 20.00
 WHERE provider = 'gemini';"""

_SETTING = r"""ALTER TABLE ai_setting
  ADD COLUMN extraction_provider ai_provider,
  ADD COLUMN assist_provider ai_provider,
  ADD COLUMN shadow_provider ai_provider,
  ADD COLUMN allow_provider_fallback BOOLEAN NOT NULL DEFAULT false;

UPDATE ai_setting
   SET assist_provider = CASE WHEN mode <> 'rules_only' THEN provider END,
       extraction_provider = CASE
         WHEN reader_strategy = 'single' THEN primary_reader
         WHEN mode <> 'rules_only' THEN provider
       END,
       shadow_provider = CASE WHEN reader_strategy = 'shadow' THEN primary_reader END;"""

_BATCH = r"""ALTER TABLE batch ADD COLUMN provider_override ai_provider;"""

_ALLOW = r"""CREATE TABLE ai_endpoint_allow (
  id            UUID NOT NULL,
  workspace_id  UUID NOT NULL REFERENCES workspace(id),
  url           TEXT NOT NULL,
  action        TEXT NOT NULL,
  added_by      UUID REFERENCES app_user(id),
  added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ai_endpoint_allow_pkey PRIMARY KEY (id),
  CONSTRAINT ai_endpoint_allow_url_ck CHECK (length(url) BETWEEN 1 AND 512),
  CONSTRAINT ai_endpoint_allow_action_ck CHECK (action IN ('add', 'remove'))
);
CREATE INDEX ai_endpoint_allow_url_ix ON ai_endpoint_allow (workspace_id, url, added_at);
CREATE TRIGGER ai_endpoint_allow_append_only BEFORE UPDATE OR DELETE ON ai_endpoint_allow
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON ai_endpoint_allow FROM reconbench_app;
REVOKE ALL ON ai_endpoint_allow FROM reconbench_report;"""

_MEASUREMENT = r"""CREATE TABLE provider_measurement (
  id            UUID NOT NULL,
  workspace_id  UUID NOT NULL REFERENCES workspace(id),
  provider      ai_provider NOT NULL,
  field_type    TEXT NOT NULL,
  split         TEXT NOT NULL,
  n             INTEGER NOT NULL,
  correct       INTEGER NOT NULL,
  wrong         INTEGER NOT NULL,
  lower_bound   d_score,
  report_hash   TEXT NOT NULL,
  epoch         TEXT,
  measured_on   DATE NOT NULL,
  imported_by   UUID REFERENCES app_user(id),
  imported_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT provider_measurement_pkey PRIMARY KEY (id),
  CONSTRAINT provider_measurement_field_ck CHECK (length(field_type) BETWEEN 1 AND 64),
  CONSTRAINT provider_measurement_split_ck CHECK (split IN ('dev', 'heldout')),
  CONSTRAINT provider_measurement_counts_ck CHECK (n >= 0 AND correct >= 0 AND wrong >= 0 AND correct + wrong <= n),
  CONSTRAINT provider_measurement_hash_ck CHECK (report_hash ~ '^[0-9a-f]{12,64}$'),
  CONSTRAINT provider_measurement_epoch_ck CHECK (epoch IS NULL OR length(epoch) BETWEEN 1 AND 128)
);
CREATE INDEX provider_measurement_lookup_ix ON provider_measurement (workspace_id, provider, field_type, measured_on DESC);
CREATE TRIGGER provider_measurement_append_only BEFORE UPDATE OR DELETE ON provider_measurement
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON provider_measurement FROM reconbench_app;
REVOKE ALL ON provider_measurement FROM reconbench_report;"""


def upgrade() -> None:
    execute_sql_block(_CONFIG)
    execute_sql_block(_SETTING)
    execute_sql_block(_BATCH)
    execute_sql_block(_ALLOW)
    execute_sql_block(_MEASUREMENT)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
