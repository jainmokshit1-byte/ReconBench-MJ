"""AI Assist request ledger, worker heartbeat, line opinions and session-bound keys.

Implements the reviewed additions in docs/decision-log.md (D4, D6, D8, D10) and
docs/schema-errata.md. Object ownership follows revision 0015: Alembic runs this
revision as reconbench_migrator, so default privileges give reconbench_app CRUD
and reconbench_report SELECT; the narrower grants are then set explicitly below.
"""

# ruff: noqa: E501 -- keep each DDL statement readable as one block.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_SECRET_MUST_BE_EMPTY = r"""DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM secret) THEN
    RAISE EXCEPTION 'revision 0017 requires an empty secret table: stored keys predate session binding and public-key sealing'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;"""

_AI_REQUEST = r"""CREATE TABLE ai_request (
  id                      UUID PRIMARY KEY,
  workspace_id            UUID NOT NULL REFERENCES workspace(id),
  call_kind               TEXT NOT NULL,
  hook                    ai_hook,
  mode                    ai_mode NOT NULL,
  subject_type            TEXT NOT NULL,
  subject_id              UUID,
  subject_version         TEXT NOT NULL,
  run_id                  UUID REFERENCES recon_run(id),
  operation_key           TEXT NOT NULL,
  requested_by            UUID REFERENCES app_user(id),
  settings_version        INTEGER NOT NULL,
  consent_fingerprint     d_hash,
  provider                ai_provider NOT NULL,
  model                   TEXT NOT NULL,
  prompt_version          TEXT NOT NULL,
  schema_version          TEXT NOT NULL,
  redaction_version       TEXT NOT NULL,
  request_key             d_hash NOT NULL,
  cache_key               d_hash,
  attempt_no              INTEGER NOT NULL DEFAULT 1,
  request_state           TEXT NOT NULL DEFAULT 'queued',
  apply_outcome           TEXT NOT NULL DEFAULT 'pending',
  skip_code               TEXT,
  error_code              TEXT,
  reserved_input_tokens   INTEGER NOT NULL DEFAULT 0,
  reserved_output_tokens  INTEGER NOT NULL DEFAULT 0,
  reservation_is_estimate BOOLEAN NOT NULL DEFAULT true,
  reserved_usd            NUMERIC(12,6),
  price_version           TEXT,
  tokens_input            INTEGER,
  tokens_output           INTEGER,
  tokens_thought          INTEGER,
  usage_known             BOOLEAN NOT NULL DEFAULT false,
  actual_usd              NUMERIC(12,6),
  ai_cache_id             UUID REFERENCES ai_cache(id) ON DELETE SET NULL,
  job_id                  BIGINT,
  lease_expires_at        TIMESTAMPTZ,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  reserved_at             TIMESTAMPTZ,
  sent_at                 TIMESTAMPTZ,
  finished_at             TIMESTAMPTZ,
  acknowledged_by         UUID REFERENCES app_user(id),
  acknowledged_at         TIMESTAMPTZ,
  UNIQUE (workspace_id, request_key, attempt_no),
  CONSTRAINT ai_request_call_kind_ck CHECK (call_kind IN ('hook', 'provider_test', 'synthetic_smoke')),
  CONSTRAINT ai_request_hook_ck CHECK (
    (call_kind = 'hook') = (hook IS NOT NULL) AND (hook IS NULL OR hook <> 'h5_explain'::ai_hook)
  ),
  CONSTRAINT ai_request_mode_ck CHECK (call_kind <> 'hook' OR mode <> 'rules_only'::ai_mode),
  CONSTRAINT ai_request_consent_ck CHECK (call_kind <> 'hook' OR consent_fingerprint IS NOT NULL),
  CONSTRAINT ai_request_subject_type_ck CHECK (
    subject_type IN ('document_version', 'recon_line_batch', 'link_resolution', 'vendor_merge_suggestion', 'fixture')
  ),
  CONSTRAINT ai_request_subject_ck CHECK ((subject_type = 'fixture') = (subject_id IS NULL)),
  CONSTRAINT ai_request_text_len_ck CHECK (
    length(subject_version) BETWEEN 1 AND 200
    AND length(operation_key) BETWEEN 1 AND 200
    AND length(model) BETWEEN 1 AND 200
    AND length(prompt_version) BETWEEN 1 AND 64
    AND length(schema_version) BETWEEN 1 AND 64
    AND length(redaction_version) BETWEEN 1 AND 64
    AND (skip_code IS NULL OR length(skip_code) BETWEEN 1 AND 64)
    AND (error_code IS NULL OR length(error_code) BETWEEN 1 AND 64)
    AND (price_version IS NULL OR length(price_version) BETWEEN 1 AND 64)
  ),
  CONSTRAINT ai_request_attempt_ck CHECK (attempt_no >= 1),
  CONSTRAINT ai_request_state_ck CHECK (
    request_state IN ('queued', 'reserved', 'sending', 'succeeded', 'failed', 'uncertain', 'released', 'skipped')
  ),
  CONSTRAINT ai_request_apply_ck CHECK (
    apply_outcome IN ('pending', 'applied', 'discarded_stale', 'discarded_revoked', 'discarded_invalid', 'not_applicable')
  ),
  CONSTRAINT ai_request_applied_ck CHECK (apply_outcome <> 'applied' OR request_state = 'succeeded'),
  CONSTRAINT ai_request_reserved_ck CHECK (
    (request_state IN ('queued', 'skipped')) = (reserved_at IS NULL)
  ),
  CONSTRAINT ai_request_sent_ck CHECK (
    (request_state IN ('sending', 'succeeded', 'failed', 'uncertain')) = (sent_at IS NOT NULL)
  ),
  CONSTRAINT ai_request_finished_ck CHECK (
    (request_state IN ('succeeded', 'failed', 'uncertain', 'released', 'skipped')) = (finished_at IS NOT NULL)
  ),
  CONSTRAINT ai_request_skip_ck CHECK ((request_state = 'skipped') = (skip_code IS NOT NULL)),
  CONSTRAINT ai_request_error_ck CHECK (request_state NOT IN ('failed', 'uncertain') OR error_code IS NOT NULL),
  CONSTRAINT ai_request_tokens_ck CHECK (
    reserved_input_tokens >= 0 AND reserved_output_tokens >= 0
    AND (tokens_input IS NULL OR tokens_input >= 0)
    AND (tokens_output IS NULL OR tokens_output >= 0)
    AND (tokens_thought IS NULL OR tokens_thought >= 0)
  ),
  CONSTRAINT ai_request_usage_ck CHECK (NOT usage_known OR (tokens_input IS NOT NULL AND tokens_output IS NOT NULL)),
  CONSTRAINT ai_request_usd_ck CHECK (
    (reserved_usd IS NULL OR reserved_usd >= 0)
    AND (actual_usd IS NULL OR actual_usd >= 0)
    AND ((reserved_usd IS NULL AND actual_usd IS NULL) OR price_version IS NOT NULL)
  ),
  CONSTRAINT ai_request_ack_ck CHECK (
    (acknowledged_by IS NULL) = (acknowledged_at IS NULL)
    AND (acknowledged_at IS NULL OR request_state = 'uncertain')
  )
);
CREATE UNIQUE INDEX ai_request_live_uq ON ai_request (workspace_id, request_key)
  WHERE request_state IN ('queued', 'reserved', 'sending')
     OR (request_state = 'uncertain' AND acknowledged_at IS NULL);
CREATE INDEX ai_request_window_ix ON ai_request (workspace_id, reserved_at) WHERE reserved_at IS NOT NULL;
CREATE INDEX ai_request_inflight_ix ON ai_request (workspace_id) WHERE request_state IN ('reserved', 'sending');
CREATE INDEX ai_request_operation_ix ON ai_request (workspace_id, operation_key);
CREATE INDEX ai_request_run_ix ON ai_request (run_id) WHERE run_id IS NOT NULL;
CREATE INDEX ai_request_cache_ix ON ai_request (workspace_id, cache_key) WHERE cache_key IS NOT NULL;
CREATE INDEX ai_request_subject_ix ON ai_request (workspace_id, subject_type, subject_id);
CREATE INDEX ai_request_sweep_ix ON ai_request (request_state, lease_expires_at)
  WHERE request_state IN ('queued', 'reserved', 'sending');"""

_AI_WORKER_HEARTBEAT = r"""CREATE TABLE ai_worker_heartbeat (
  worker_role   TEXT PRIMARY KEY,
  instance      TEXT NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ai_worker_heartbeat_role_ck CHECK (worker_role = 'ai'),
  CONSTRAINT ai_worker_heartbeat_instance_ck CHECK (length(instance) BETWEEN 1 AND 200)
);"""

_AI_CALL_LOG = r"""ALTER TABLE ai_call_log ADD COLUMN ai_request_id UUID REFERENCES ai_request(id);
ALTER TABLE ai_call_log ADD COLUMN tokens_thought INTEGER;
ALTER TABLE ai_call_log ADD CONSTRAINT ai_call_log_tokens_thought_ck CHECK (tokens_thought IS NULL OR tokens_thought >= 0);
CREATE INDEX ai_call_log_request_ix ON ai_call_log (ai_request_id) WHERE ai_request_id IS NOT NULL;"""

_AI_LINE_OPINION = r"""CREATE TABLE ai_line_opinion (
  id                    UUID PRIMARY KEY,
  workspace_id          UUID NOT NULL REFERENCES workspace(id),
  run_id                UUID NOT NULL REFERENCES recon_run(id),
  recon_line_result_id  UUID NOT NULL REFERENCES recon_line_result(id),
  invoice_line_id       UUID NOT NULL REFERENCES doc_line(id),
  po_doc_line_id        UUID NOT NULL REFERENCES doc_line(id),
  ai_request_id         UUID NOT NULL REFERENCES ai_request(id),
  ai_call_id            UUID NOT NULL REFERENCES ai_call_log(id),
  opinion               ai_opinion NOT NULL,
  reason                TEXT NOT NULL,
  model                 TEXT NOT NULL,
  prompt_version        TEXT NOT NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, recon_line_result_id),
  CONSTRAINT ai_line_opinion_reason_ck CHECK (length(reason) BETWEEN 1 AND 300),
  CONSTRAINT ai_line_opinion_lines_ck CHECK (invoice_line_id <> po_doc_line_id)
);
CREATE INDEX ai_line_opinion_run_ix ON ai_line_opinion (run_id);
CREATE TRIGGER ai_line_opinion_append_only BEFORE UPDATE OR DELETE ON ai_line_opinion
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER ai_line_opinion_append_only_truncate BEFORE TRUNCATE ON ai_line_opinion
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();"""

_SESSION_BOUND_SECRET = r"""ALTER TABLE session ADD CONSTRAINT session_workspace_id_id_user_id_key UNIQUE (workspace_id, id, user_id);
ALTER TABLE secret ALTER COLUMN created_by SET NOT NULL;
ALTER TABLE secret ADD COLUMN session_id UUID;
ALTER TABLE secret ADD CONSTRAINT secret_session_fkey
  FOREIGN KEY (workspace_id, session_id, created_by) REFERENCES session (workspace_id, id, user_id);
ALTER TABLE secret ADD CONSTRAINT secret_session_binding_ck CHECK ((session_id IS NULL) = (expires_at IS NULL));
CREATE INDEX secret_session_ix ON secret (session_id) WHERE session_id IS NOT NULL;"""

_LINK_AI_CALL = (
    r"""CREATE UNIQUE INDEX link_ai_call_uq ON link (ai_call_id) WHERE ai_call_id IS NOT NULL;"""
)

_AI_SETTING_CONSENT = r"""UPDATE ai_setting
   SET mode = 'rules_only', consent_given = false, version = version + 1, updated_at = now()
 WHERE consent_given;
ALTER TABLE ai_setting ADD COLUMN consent_fingerprint d_hash;
ALTER TABLE ai_setting ADD CONSTRAINT ai_setting_consent_ck CHECK (
  NOT consent_given
  OR (consent_fingerprint IS NOT NULL AND consent_by IS NOT NULL AND consent_at IS NOT NULL
      AND consent_text_version IS NOT NULL)
);"""

_GRANTS = r"""REVOKE UPDATE, DELETE, TRUNCATE ON ai_line_opinion FROM reconbench_app;
REVOKE ALL ON ai_line_opinion FROM reconbench_report;
REVOKE SELECT ON ai_cache FROM reconbench_report;"""


def upgrade() -> None:
    execute_sql_block(_SECRET_MUST_BE_EMPTY)
    execute_sql_block(_AI_REQUEST)
    execute_sql_block(_AI_WORKER_HEARTBEAT)
    execute_sql_block(_AI_CALL_LOG)
    execute_sql_block(_AI_LINE_OPINION)
    execute_sql_block(_SESSION_BOUND_SECRET)
    execute_sql_block(_LINK_AI_CALL)
    execute_sql_block(_AI_SETTING_CONSENT)
    execute_sql_block(_GRANTS)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
