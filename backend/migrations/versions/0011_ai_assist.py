"""4.10 AI assist migration, sourced from Backend Schema §4.10."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0011"
down_revision = '0010'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 1390
    r"""CREATE TABLE secret (
  id            UUID PRIMARY KEY,
  workspace_id  UUID NOT NULL REFERENCES workspace(id),
  purpose       TEXT NOT NULL CHECK (purpose IN ('ai_api_key')),
  ciphertext    BYTEA NOT NULL,                     -- AES-256-GCM output, auth tag included
  nonce         BYTEA NOT NULL,
  key_id        TEXT NOT NULL,
  key_hint      TEXT,                               -- masked, e.g. '****a3f9'
  expires_at    TIMESTAMPTZ,                        -- set for session-only storage
  created_by    UUID REFERENCES app_user(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  rotated_at    TIMESTAMPTZ
);
CREATE INDEX secret_expiry_ix ON secret (expires_at) WHERE expires_at IS NOT NULL;
""",

    # Backend Schema source line 1410
    r"""CREATE TABLE ai_setting (
  id                     UUID PRIMARY KEY,
  workspace_id           UUID NOT NULL REFERENCES workspace(id),
  mode                   ai_mode NOT NULL DEFAULT 'rules_only',
  provider               ai_provider,
  model                  TEXT,                          -- a setting, not code (TRD §4)
  budget_cap_usd         NUMERIC(12,4) CHECK (budget_cap_usd IS NULL OR budget_cap_usd >= 0),
  key_storage            ai_key_storage NOT NULL DEFAULT 'session',
  key_secret_id          UUID REFERENCES secret(id),
  consent_given          BOOLEAN NOT NULL DEFAULT false,
  consent_by             UUID REFERENCES app_user(id),
  consent_at             TIMESTAMPTZ,
  consent_text_version   TEXT,                          -- the consent text names the provider
  redaction_enabled      BOOLEAN NOT NULL DEFAULT true, -- bank account, IFSC, phone, email removed before any call
  auto_accept_ai_links   BOOLEAN NOT NULL DEFAULT false,
  auto_accept_threshold  d_score,
  status_code            TEXT NOT NULL DEFAULT 'RULES_ONLY',   -- pill: RULES_ONLY, CONNECTED, or an error code
  last_test_at           TIMESTAMPTZ,
  last_test_ok           BOOLEAN,
  updated_by             UUID REFERENCES app_user(id),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  version                INTEGER NOT NULL DEFAULT 1,
  UNIQUE (workspace_id),
  CONSTRAINT ai_setting_mode_ck CHECK (mode = 'rules_only' OR (consent_given AND provider IS NOT NULL AND model IS NOT NULL)),
  CONSTRAINT ai_setting_auto_ck CHECK (NOT auto_accept_ai_links OR auto_accept_threshold IS NOT NULL)
);
""",

    # Backend Schema source line 1444
    r"""CREATE TABLE ai_call_log (
  id                   UUID PRIMARY KEY,
  workspace_id         UUID NOT NULL REFERENCES workspace(id),
  run_id               UUID REFERENCES recon_run(id),          -- NULL for hook H1 at parse time
  document_id          UUID REFERENCES document(id),
  document_version_id  UUID REFERENCES document_version(id),
  hook                 ai_hook NOT NULL,
  provider             ai_provider NOT NULL,
  model                TEXT NOT NULL,
  prompt_version       TEXT NOT NULL,
  input_hash           d_hash NOT NULL,
  outcome              ai_outcome NOT NULL,
  error_code           TEXT,
  redacted             BOOLEAN NOT NULL,
  tokens_in            INTEGER,
  tokens_out           INTEGER,
  cost_usd             NUMERIC(12,6),
  latency_ms           INTEGER,
  job_id               BIGINT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ai_call_log_run_ix  ON ai_call_log (run_id) WHERE run_id IS NOT NULL;
CREATE INDEX ai_call_log_doc_ix  ON ai_call_log (document_id);
CREATE INDEX ai_call_log_time_ix ON ai_call_log (workspace_id, created_at DESC);
""",

    # Backend Schema source line 1474
    r"""CREATE TABLE ai_cache (
  id              UUID PRIMARY KEY,
  workspace_id    UUID NOT NULL REFERENCES workspace(id),
  cache_key       d_hash NOT NULL,          -- SHA-256 of document hash + hook + prompt version + model (+ candidate set for H2 to H4)
  hook            ai_hook NOT NULL,
  model           TEXT NOT NULL,
  prompt_version  TEXT NOT NULL,
  document_hash   d_hash,
  response        JSONB NOT NULL,
  hit_count       INTEGER NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_hit_at     TIMESTAMPTZ,
  UNIQUE (workspace_id, cache_key)
);
""",

    # Backend Schema source line 1494
    r"""ALTER TABLE field_evidence          ADD COLUMN ai_call_id UUID REFERENCES ai_call_log(id);   -- H1 fields
ALTER TABLE link                    ADD COLUMN ai_call_id UUID REFERENCES ai_call_log(id);   -- H3 links
ALTER TABLE vendor_merge_suggestion ADD COLUMN ai_call_id UUID REFERENCES ai_call_log(id);   -- H4 opinions
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
