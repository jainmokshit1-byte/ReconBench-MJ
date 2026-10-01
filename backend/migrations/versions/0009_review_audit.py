"""4.8 Review, effective status and audit migration, sourced from Backend Schema §4.8."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0009"
down_revision = '0008'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 1156
    r"""CREATE TABLE case_action (
  id                 UUID PRIMARY KEY,
  workspace_id       UUID NOT NULL REFERENCES workspace(id),
  invoice_id         UUID NOT NULL REFERENCES document(id),
  case_id            UUID REFERENCES recon_case(id),        -- the case the person was looking at
  action             action_type NOT NULL,
  reason             TEXT,
  note               TEXT,
  link_id            UUID REFERENCES link(id),              -- confirm_link / change_link: the link version written
  actor_id           UUID NOT NULL REFERENCES app_user(id),
  actor_role         user_role NOT NULL,                    -- role at the time; roles can change later
  computed_status    case_status,                           -- status when the action was taken
  reason_codes       TEXT[] NOT NULL DEFAULT '{}',          -- snapshot, for calibration (TR-OPS-7)
  input_fingerprint  d_hash,                                -- stored from P0; staleness check is P1 (TR-REV-7)
  bulk_id            UUID,                                  -- groups the items of one bulk action
  occurred_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT case_action_approve_ck CHECK (action <> 'approve' OR length(btrim(coalesce(reason, ''))) > 0),
  CONSTRAINT case_action_note_ck    CHECK (action <> 'note'    OR length(btrim(coalesce(note, ''))) > 0),
  CONSTRAINT case_action_link_ck    CHECK (action NOT IN ('confirm_link','change_link') OR link_id IS NOT NULL),
  CONSTRAINT case_action_print_ck   CHECK (action NOT IN ('approve','hold','reject') OR input_fingerprint IS NOT NULL)
);
CREATE INDEX case_action_invoice_ix ON case_action (invoice_id, occurred_at DESC);
CREATE INDEX case_action_actor_ix   ON case_action (actor_id, occurred_at DESC);
CREATE INDEX case_action_bulk_ix    ON case_action (bulk_id) WHERE bulk_id IS NOT NULL;
""",

    # Backend Schema source line 1188
    r"""CREATE TABLE case_current (
  id                UUID PRIMARY KEY,
  workspace_id      UUID NOT NULL REFERENCES workspace(id),
  invoice_id        UUID NOT NULL REFERENCES document(id),
  case_id           UUID NOT NULL REFERENCES recon_case(id),
  run_id            UUID NOT NULL REFERENCES recon_run(id),
  vendor_id         UUID REFERENCES vendor(id),               -- as at the run
  invoice_date      DATE,
  computed_status   case_status NOT NULL,
  overlay           overlay_type,                             -- approved | on_hold | rejected | NULL
  effective_status  TEXT NOT NULL,                            -- overlay if present, else computed status
  severity_rank     SMALLINT NOT NULL,                        -- default list order, from the computed status
  human_touched     BOOLEAN NOT NULL DEFAULT false,           -- any action ever recorded on this invoice
  latest_action_id  UUID REFERENCES case_action(id),
  version           INTEGER NOT NULL DEFAULT 1,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, invoice_id),
  CONSTRAINT case_current_effective_ck CHECK (effective_status IN
    ('duplicate','unlinked','needs_review','exception','pending','matched_flags','matched','approved','on_hold','rejected'))
);
CREATE INDEX case_current_list_ix   ON case_current (workspace_id, severity_rank, invoice_date, invoice_id);
CREATE INDEX case_current_status_ix ON case_current (workspace_id, effective_status, invoice_date);
CREATE INDEX case_current_vendor_ix ON case_current (workspace_id, vendor_id, effective_status);
CREATE INDEX case_current_run_ix    ON case_current (run_id);
""",

    # Backend Schema source line 1219
    r"""CREATE FUNCTION severity_rank(s case_status) RETURNS SMALLINT
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE s WHEN 'exception'     THEN 1 WHEN 'needs_review' THEN 2 WHEN 'unlinked' THEN 3
                WHEN 'duplicate'     THEN 4 WHEN 'pending'      THEN 5
                WHEN 'matched_flags' THEN 6 WHEN 'matched'      THEN 7 END::SMALLINT
$$;

-- The overlay is decided by the latest approve / hold / reject / release on the invoice.
-- Left VOLATILE on purpose, so the trigger below sees the row it has just inserted.
CREATE FUNCTION case_overlay(p_invoice UUID) RETURNS overlay_type
LANGUAGE sql AS $$
  SELECT CASE a.action WHEN 'approve' THEN 'approved'::overlay_type
                       WHEN 'hold'    THEN 'on_hold'::overlay_type
                       WHEN 'reject'  THEN 'rejected'::overlay_type END      -- release gives NULL
    FROM case_action a
   WHERE a.invoice_id = p_invoice AND a.action IN ('approve','hold','reject','release')
   ORDER BY a.occurred_at DESC
   LIMIT 1
$$;

CREATE FUNCTION case_action_apply() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_overlay overlay_type := case_overlay(NEW.invoice_id);
BEGIN
  UPDATE case_current cc
     SET overlay          = v_overlay,
         effective_status = COALESCE(v_overlay::text, cc.computed_status::text),
         human_touched    = true,
         latest_action_id = NEW.id,
         version          = cc.version + 1,
         updated_at       = now()
   WHERE cc.invoice_id = NEW.invoice_id;
  RETURN NULL;
END $$;

CREATE TRIGGER case_action_apply AFTER INSERT ON case_action
  FOR EACH ROW EXECUTE FUNCTION case_action_apply();
""",

    # Backend Schema source line 1286
    r"""CREATE SEQUENCE audit_log_seq;

CREATE TABLE audit_log (
  id            UUID PRIMARY KEY,
  workspace_id  UUID NOT NULL REFERENCES workspace(id),
  seq           BIGINT NOT NULL,                    -- set by the trigger, under the chain lock
  actor_id      UUID REFERENCES app_user(id),       -- NULL for system jobs such as retention
  actor_role    user_role,
  action        TEXT NOT NULL,                      -- vocabulary below
  target_type   TEXT NOT NULL,                      -- 'case', 'link', 'vendor', 'file', 'config', 'ai_setting', 'export', 'user'
  target_id     UUID,
  before_state  JSONB,
  after_state   JSONB,
  reason        TEXT,
  occurred_at   TIMESTAMPTZ NOT NULL,               -- set by the trigger
  prev_hash     d_hash,                             -- NULL only for a workspace's first row
  row_hash      d_hash NOT NULL,                    -- set by the trigger
  UNIQUE (seq)
);
CREATE INDEX audit_log_chain_ix  ON audit_log (workspace_id, seq DESC);
CREATE INDEX audit_log_target_ix ON audit_log (workspace_id, target_type, target_id, seq DESC);
CREATE INDEX audit_log_actor_ix  ON audit_log (workspace_id, actor_id, seq DESC);

CREATE FUNCTION audit_log_chain() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_prev TEXT;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('reconbench.audit_log.' || NEW.workspace_id::text, 0));
  SELECT a.row_hash INTO v_prev
    FROM audit_log a
   WHERE a.workspace_id = NEW.workspace_id
   ORDER BY a.seq DESC
   LIMIT 1;
  NEW.seq         := nextval('audit_log_seq');
  NEW.occurred_at := clock_timestamp();
  NEW.prev_hash   := v_prev;
  NEW.row_hash    := encode(sha256(convert_to(concat_ws('|',
                       COALESCE(v_prev, ''),
                       NEW.seq::text,
                       NEW.workspace_id::text,
                       COALESCE(NEW.actor_id::text, ''),
                       NEW.action,
                       NEW.target_type,
                       COALESCE(NEW.target_id::text, ''),
                       COALESCE(NEW.before_state::text, ''),
                       COALESCE(NEW.after_state::text, ''),
                       COALESCE(NEW.reason, ''),
                       to_char(NEW.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
                     ), 'UTF8')), 'hex');
  RETURN NEW;
END $$;

CREATE TRIGGER audit_log_chain BEFORE INSERT ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_chain();
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
