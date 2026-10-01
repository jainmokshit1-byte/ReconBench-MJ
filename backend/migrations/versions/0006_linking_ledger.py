"""4.5 Linking and ledger migration, sourced from Backend Schema §4.5."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0006"
down_revision = '0005'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 753
    r"""CREATE TABLE link (
  id                  UUID PRIMARY KEY,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  link_type           link_type NOT NULL,
  level               link_level NOT NULL,
  from_document_id    UUID NOT NULL REFERENCES document(id),   -- invoice or GRN
  from_line_no        INTEGER,                                 -- NULL = header level
  to_document_id      UUID NOT NULL REFERENCES document(id),   -- PO, or GRN for inv_grn
  version_no          INTEGER NOT NULL,                        -- 1, 2, 3 … per logical link
  supersedes_link_id  UUID REFERENCES link(id),
  method              link_method NOT NULL,                    -- ladder step 0,1,2,3,4
  confidence          d_score NOT NULL,
  state               link_state NOT NULL,
  ai_suggested        BOOLEAN NOT NULL DEFAULT false,          -- hook H3 chose it; ai_call_id added in §4.10
  evidence_snippet    TEXT,
  evidence_page       INTEGER,
  evidence            JSONB,                                   -- §5.4
  detail_code         TEXT,                                    -- e.g. PO_NOT_IN_POOL
  created_by          UUID REFERENCES app_user(id),            -- NULL when the ladder wrote it
  confirmed_by        UUID REFERENCES app_user(id),
  confirmed_at        TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT link_level_ck     CHECK ((level = 'line') = (from_line_no IS NOT NULL)),
  CONSTRAINT link_not_self_ck  CHECK (from_document_id <> to_document_id),
  CONSTRAINT link_manual_ck    CHECK (method <> 'manual' OR confidence = 1),
  CONSTRAINT link_confirmed_ck CHECK (state <> 'confirmed' OR confirmed_by IS NOT NULL),
  UNIQUE NULLS NOT DISTINCT (workspace_id, link_type, from_document_id, from_line_no, to_document_id, version_no)
);
CREATE INDEX link_to_state_ix ON link (to_document_id, state);
CREATE INDEX link_from_ix     ON link (from_document_id);
""",

    # Backend Schema source line 801
    r"""CREATE TABLE link_current (
  id                UUID PRIMARY KEY,
  workspace_id      UUID NOT NULL REFERENCES workspace(id),
  link_type         link_type NOT NULL,
  from_document_id  UUID NOT NULL REFERENCES document(id),
  from_line_no      INTEGER,
  to_document_id    UUID NOT NULL REFERENCES document(id),
  current_link_id   UUID NOT NULL REFERENCES link(id),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (workspace_id, link_type, from_document_id, from_line_no, to_document_id)
);
CREATE INDEX link_current_to_ix   ON link_current (to_document_id);
CREATE INDEX link_current_from_ix ON link_current (from_document_id);
""",

    # Backend Schema source line 822
    r"""CREATE TABLE link_resolution (
  id             UUID PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES workspace(id),
  document_id    UUID NOT NULL REFERENCES document(id),     -- invoice or GRN
  outcome        resolution_outcome NOT NULL,
  resolved_step  SMALLINT CHECK (resolved_step BETWEEN 0 AND 4),
  detail_code    TEXT,                                      -- PO_NOT_IN_POOL, AMBIGUOUS, NO_EVIDENCE
  cited_refs     TEXT[] NOT NULL DEFAULT '{}',              -- PO references read or found, as printed
  candidates     JSONB NOT NULL DEFAULT '[]',               -- top 3 from Step 3, §5.4
  input_hash     d_hash NOT NULL,                           -- idempotency key part: vendor id + input hash
  computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, document_id)
);
""",

    # Backend Schema source line 841
    r"""CREATE TABLE opening_balance (
  id               UUID PRIMARY KEY,
  workspace_id     UUID NOT NULL REFERENCES workspace(id),
  source_file_id   UUID NOT NULL REFERENCES source_file(id),   -- zone = 'opening_balance'
  row_no           INTEGER NOT NULL,
  vendor_id        UUID REFERENCES vendor(id),
  po_no_raw        TEXT NOT NULL,
  canonical_po_no  TEXT NOT NULL,
  po_line_no       INTEGER NOT NULL,
  po_document_id   UUID REFERENCES document(id),
  received_qty     d_qty NOT NULL DEFAULT 0,
  billed_qty       d_qty NOT NULL DEFAULT 0,
  as_of_date       DATE NOT NULL,
  withdrawn_at     TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_file_id, row_no)
);
CREATE INDEX opening_balance_po_ix ON opening_balance (workspace_id, canonical_po_no, po_line_no) WHERE withdrawn_at IS NULL;
""",

    # Backend Schema source line 865
    r"""CREATE TABLE po_line_ledger (
  id                    UUID PRIMARY KEY,
  workspace_id          UUID NOT NULL REFERENCES workspace(id),
  po_document_id        UUID NOT NULL REFERENCES document(id),
  po_version_id         UUID NOT NULL REFERENCES document_version(id),   -- governing version used
  po_line_no            INTEGER NOT NULL,
  po_doc_line_id        UUID NOT NULL REFERENCES doc_line(id),
  uom                   TEXT,
  po_rate               d_qty,
  ordered               d_qty NOT NULL,
  received_accepted     d_qty NOT NULL DEFAULT 0,                        -- includes opening balance
  received_rejected     d_qty NOT NULL DEFAULT 0,
  billed                d_qty NOT NULL DEFAULT 0,                        -- every invoice except Duplicate and Rejected (OQ-1)
  open_qty              NUMERIC(18,4) GENERATED ALWAYS AS (ordered - billed) STORED,   -- negative = over-billed
  grni_qty              NUMERIC(18,4) GENERATED ALWAYS AS (GREATEST(received_accepted - billed, 0)) STORED,
  completeness_warning  BOOLEAN NOT NULL DEFAULT false,                  -- earlier months may be missing (TR-REC-7)
  input_hash            d_hash NOT NULL,
  computed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, po_document_id, po_line_no)
);
CREATE INDEX po_line_ledger_grni_ix ON po_line_ledger (workspace_id, po_document_id) WHERE grni_qty > 0;
""",

    # Backend Schema source line 894
    r"""CREATE TABLE ledger_entry (
  id                  UUID PRIMARY KEY,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  po_line_ledger_id   UUID NOT NULL REFERENCES po_line_ledger(id) ON DELETE CASCADE,
  kind                TEXT NOT NULL CHECK (kind IN ('opening','grn','invoice')),
  seq                 INTEGER NOT NULL,                 -- processing order on the PO line
  source_document_id  UUID REFERENCES document(id),
  source_doc_line_id  UUID REFERENCES doc_line(id),
  opening_balance_id  UUID REFERENCES opening_balance(id),
  sort_date           DATE,                             -- invoice date or GRN date
  sort_no             TEXT,                             -- canonical document number
  sort_key            d_hash,                           -- natural key, the last tie-break (TR-REC-2)
  accepted_delta      d_qty NOT NULL DEFAULT 0,
  rejected_delta      d_qty NOT NULL DEFAULT 0,
  billed_delta        d_qty NOT NULL DEFAULT 0,
  consumes            BOOLEAN NOT NULL DEFAULT true,
  excluded_reason     TEXT CHECK (excluded_reason IN ('duplicate','rejected')),
  alignment_score     d_score,
  alignment_state     alignment_state,
  CONSTRAINT ledger_entry_source_ck CHECK (
        (kind = 'opening' AND opening_balance_id IS NOT NULL AND source_doc_line_id IS NULL)
     OR (kind IN ('grn','invoice') AND source_doc_line_id IS NOT NULL AND opening_balance_id IS NULL)),
  CONSTRAINT ledger_entry_consumes_ck CHECK (consumes = (excluded_reason IS NULL)),
  UNIQUE (po_line_ledger_id, seq)
);
CREATE UNIQUE INDEX ledger_entry_line_uq ON ledger_entry (po_line_ledger_id, source_doc_line_id) WHERE source_doc_line_id IS NOT NULL;
CREATE INDEX ledger_entry_doc_ix ON ledger_entry (source_document_id);
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
