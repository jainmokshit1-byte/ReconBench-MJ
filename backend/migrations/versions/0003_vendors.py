"""4.2 Vendors migration, sourced from Backend Schema §4.2."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0003"
down_revision = '0002'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 322
    r"""CREATE TABLE vendor (
  id               UUID PRIMARY KEY,
  workspace_id     UUID NOT NULL REFERENCES workspace(id),
  canonical_name   TEXT NOT NULL,
  normalized_name  TEXT NOT NULL,                     -- M/S, PVT, PRIVATE, LTD, LIMITED, LLP and punctuation removed
  pan              TEXT,                              -- GSTIN characters 3–12
  anchor           vendor_anchor NOT NULL,            -- what identifies it: gstin, pan, or name only
  created_by_rule  vendor_rule,                       -- how it first came to exist (proposed)
  merged_into      UUID REFERENCES vendor(id),
  merged_at        TIMESTAMPTZ,
  merged_by        UUID REFERENCES app_user(id),
  input_seq        BIGINT NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  version          INTEGER NOT NULL DEFAULT 1,
  CONSTRAINT vendor_no_self_merge CHECK (merged_into IS NULL OR merged_into <> id)
);
CREATE INDEX vendor_active_ix ON vendor (workspace_id) WHERE merged_into IS NULL;
CREATE INDEX vendor_pan_ix    ON vendor (workspace_id, pan) WHERE pan IS NOT NULL;
CREATE INDEX vendor_name_trgm ON vendor USING gin (normalized_name gin_trgm_ops);
""",

    # Backend Schema source line 350
    r"""CREATE TABLE vendor_gstin (
  id             UUID PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES workspace(id),
  vendor_id      UUID NOT NULL REFERENCES vendor(id),
  gstin          TEXT NOT NULL,                       -- uppercase, spaces removed
  state_code     TEXT NOT NULL,                       -- characters 1–2
  pan            TEXT NOT NULL,                       -- characters 3–12
  checksum_ok    BOOLEAN NOT NULL,                    -- pattern and checksum passed
  first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, gstin)
);
CREATE INDEX vendor_gstin_vendor_ix ON vendor_gstin (vendor_id);
CREATE INDEX vendor_gstin_pan_ix    ON vendor_gstin (workspace_id, pan);
""",

    # Backend Schema source line 369
    r"""CREATE TABLE vendor_merge_suggestion (
  id                  UUID PRIMARY KEY,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  vendor_a_id         UUID NOT NULL REFERENCES vendor(id),
  vendor_b_id         UUID NOT NULL REFERENCES vendor(id),
  score               d_score NOT NULL,
  state               suggestion_state NOT NULL DEFAULT 'pending',
  ai_opinion          ai_opinion,
  ai_reason           TEXT,
  survivor_vendor_id  UUID REFERENCES vendor(id),     -- set when accepted
  decided_by          UUID REFERENCES app_user(id),
  decided_at          TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  version             INTEGER NOT NULL DEFAULT 1,
  CONSTRAINT vms_pair_order CHECK (vendor_a_id < vendor_b_id)   -- one row per unordered pair
);
CREATE UNIQUE INDEX vms_pair_uq    ON vendor_merge_suggestion (workspace_id, vendor_a_id, vendor_b_id);
CREATE INDEX        vms_pending_ix ON vendor_merge_suggestion (workspace_id) WHERE state = 'pending';
""",

    # Backend Schema source line 393
    r"""CREATE TABLE vendor_alias (
  id             UUID PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES workspace(id),
  vendor_id      UUID NOT NULL REFERENCES vendor(id),
  alias_raw      TEXT NOT NULL,
  alias_norm     TEXT NOT NULL,
  source         alias_source NOT NULL,
  suggestion_id  UUID REFERENCES vendor_merge_suggestion(id),
  created_by     UUID REFERENCES app_user(id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, alias_norm)
);
CREATE INDEX vendor_alias_vendor_ix ON vendor_alias (vendor_id);
CREATE INDEX vendor_alias_trgm      ON vendor_alias USING gin (alias_norm gin_trgm_ops);
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
