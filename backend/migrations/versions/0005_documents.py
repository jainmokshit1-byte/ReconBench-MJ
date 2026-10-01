"""4.4 Documents migration, sourced from Backend Schema §4.4."""

# ruff: noqa: E501 -- preserve the validated SQL source verbatim.

from __future__ import annotations

from reconbench_app.db.migration_sql import execute_sql_block

revision = "0005"
down_revision = '0004'
branch_labels = None
depends_on = None

SQL_BLOCKS = (
    # Backend Schema source line 512
    r"""CREATE TABLE document (
  id                  UUID PRIMARY KEY,
  workspace_id        UUID NOT NULL REFERENCES workspace(id),
  doc_type            doc_type NOT NULL,
  natural_key         d_hash NOT NULL,
  batch_id            UUID REFERENCES batch(id),        -- batch of first ingestion
  current_version_id  UUID,                              -- pointer; foreign key added below

  vendor_identity     TEXT,                              -- GSTIN, else normalised name, as read

  -- vendor resolution: derived, re-run on merge or alias change (TR-VEN-1)
  vendor_id           UUID REFERENCES vendor(id),        -- NULL = the Unassigned bucket
  vendor_rule         vendor_rule NOT NULL DEFAULT 'none',
  vendor_score        d_score,
  vendor_locked       BOOLEAN NOT NULL DEFAULT false,    -- manual assignment: re-resolution leaves it alone (proposed)
  vendor_resolved_at  TIMESTAMPTZ,

  -- projections of the current version, kept in step with the pointer (proposed)
  doc_no              TEXT,
  canonical_doc_no    TEXT,
  doc_date            DATE,
  grand_total         d_amount,
  needs_verification  BOOLEAN NOT NULL DEFAULT false,

  -- lifecycle
  withdrawn_at        TIMESTAMPTZ,
  withdrawn_by        UUID REFERENCES app_user(id),
  input_seq           BIGINT NOT NULL DEFAULT 0,         -- PO scope sequence; used when doc_type = 'po'
  first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  version             INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX document_natural_uq ON document (workspace_id, natural_key);
CREATE INDEX document_vendor_ix ON document (workspace_id, doc_type, vendor_id, doc_date);
CREATE INDEX document_live_ix   ON document (workspace_id, vendor_id, doc_type) WHERE withdrawn_at IS NULL;
CREATE INDEX document_canon_trgm ON document USING gin (canonical_doc_no gin_trgm_ops);
CREATE INDEX document_batch_ix  ON document (batch_id);
""",

    # Backend Schema source line 555
    r"""CREATE TABLE document_version (
  id                            UUID PRIMARY KEY,
  workspace_id                  UUID NOT NULL REFERENCES workspace(id),
  document_id                   UUID NOT NULL REFERENCES document(id),
  seq                           INTEGER NOT NULL,          -- 1, 2, 3 … per document
  revision                      TEXT,                      -- business revision (PO amendments)
  revision_rank                 INTEGER NOT NULL DEFAULT 0,-- parsed ordering of the revision
  content_hash                  d_hash NOT NULL,

  -- header fields (TRD Appendix A)
  doc_no                        TEXT,
  canonical_doc_no              TEXT,
  doc_date                      DATE,
  po_status                     po_status,                 -- PO only; drives PO_NOT_OPEN
  vendor_name                   TEXT,                      -- as printed; a Tally ledger name on POs
  vendor_name_norm              TEXT,
  vendor_gstin                  TEXT,
  vendor_pan                    TEXT,
  vendor_state_code             TEXT,
  buyer_gstin                   TEXT,
  ship_to                       TEXT,
  place_of_supply               TEXT,
  payment_terms_text            TEXT,                      -- as read, e.g. "Net 30"
  payment_terms_days            INTEGER,
  delivery_date                 DATE,
  currency                      CHAR(3) NOT NULL DEFAULT 'INR',
  taxable_total                 d_amount,
  cgst_total                    d_amount,
  sgst_total                    d_amount,
  igst_total                    d_amount,
  round_off                     d_amount,
  grand_total                   d_amount,

  -- linking references (as read, plus canonical forms for lookups)
  po_refs                       TEXT[] NOT NULL DEFAULT '{}',
  canonical_po_refs             TEXT[] NOT NULL DEFAULT '{}',
  grn_refs                      TEXT[] NOT NULL DEFAULT '{}',
  canonical_grn_refs            TEXT[] NOT NULL DEFAULT '{}',
  challan_no                    TEXT,
  canonical_challan_no          TEXT,
  e_way_bill_no                 TEXT,
  irn                           TEXT,                      -- strongest duplicate key
  supplier_invoice_no           TEXT,                      -- GRN: the vendor's invoice / challan number
  canonical_supplier_invoice_no TEXT,

  -- extraction
  extraction_method             extraction_method NOT NULL,
  extraction_conf               d_score,                   -- minimum over critical fields (TR-PAR-6)
  needs_verification            BOOLEAN NOT NULL DEFAULT false,
  verification_reasons          TEXT[] NOT NULL DEFAULT '{}',  -- e.g. NO_TEXT_LAYER, missing:invoice_no, low:grand_total
  extras                        JSONB NOT NULL DEFAULT '{}',   -- unknown tags and columns, never dropped

  -- provenance
  source_file_id                UUID NOT NULL REFERENCES source_file(id),
  locator                       JSONB,                     -- {"xpath":…} | {"rows":[a,b]} | {"pages":[a,b]}
  page_count                    INTEGER,
  parser_version                TEXT NOT NULL,
  layout_signature              TEXT,
  derived_from_version_id       UUID REFERENCES document_version(id),  -- manual correction, P1
  created_by                    UUID REFERENCES app_user(id),          -- NULL when a parser created it
  created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, seq)
);
CREATE INDEX document_version_governing_ix ON document_version (document_id, revision_rank DESC, seq DESC);
CREATE INDEX document_version_file_ix      ON document_version (source_file_id);
CREATE INDEX document_version_irn_ix       ON document_version (workspace_id, irn) WHERE irn IS NOT NULL;
CREATE INDEX document_version_po_refs_ix   ON document_version USING gin (canonical_po_refs);

ALTER TABLE document
  ADD CONSTRAINT document_current_version_fk
  FOREIGN KEY (current_version_id) REFERENCES document_version(id)
  DEFERRABLE INITIALLY DEFERRED;
""",

    # Backend Schema source line 638
    r"""CREATE TABLE doc_line (
  id                   UUID PRIMARY KEY,
  workspace_id         UUID NOT NULL REFERENCES workspace(id),
  document_version_id  UUID NOT NULL REFERENCES document_version(id),
  line_no              INTEGER NOT NULL,
  line_type            line_type NOT NULL DEFAULT 'goods',   -- charge lines skip item and quantity checks
  description          TEXT,
  description_norm     TEXT,                                 -- lower-cased, punctuation stripped
  tokens               TEXT[] NOT NULL DEFAULT '{}',
  spec_tokens          TEXT[] NOT NULL DEFAULT '{}',         -- model no., GB/TB, inch, kW, size, colour: the alignment guard
  item_code            TEXT,
  hsn_sac              TEXT,
  uom                  TEXT,                                 -- canonical unit
  uom_as_read          TEXT,
  quantity             d_qty,
  rate                 d_qty,
  discount             d_amount,
  amount               d_amount,
  tax_rate             d_taxrate,
  cgst_amount          d_amount,
  sgst_amount          d_amount,
  igst_amount          d_amount,
  po_ref               TEXT,                                 -- line-level PO reference, as read
  canonical_po_ref     TEXT,
  po_line_ref          TEXT,                                 -- GRN lines: the PO line they cite
  received_qty         d_qty,
  rejected_qty         d_qty,
  accepted_qty         d_qty,
  extras               JSONB NOT NULL DEFAULT '{}',
  UNIQUE (document_version_id, line_no)
);
""",

    # Backend Schema source line 675
    r"""CREATE TABLE document_text (
  document_version_id  UUID NOT NULL REFERENCES document_version(id),
  page                 INTEGER NOT NULL,
  workspace_id         UUID NOT NULL REFERENCES workspace(id),
  content              TEXT NOT NULL,
  char_count           INTEGER NOT NULL,
  is_ocr               BOOLEAN NOT NULL DEFAULT false,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (document_version_id, page)
);
""",

    # Backend Schema source line 691
    r"""CREATE TABLE field_evidence (
  id                   UUID PRIMARY KEY,
  workspace_id         UUID NOT NULL REFERENCES workspace(id),
  document_version_id  UUID REFERENCES document_version(id),
  doc_line_id          UUID REFERENCES doc_line(id),
  field_name           TEXT NOT NULL,                        -- canonical key, e.g. 'invoice_no', 'quantity'
  value_text           TEXT,                                 -- value as text; the typed copy lives on the row
  confidence           d_score NOT NULL,
  source               field_source NOT NULL,
  page                 INTEGER,
  bbox                 JSONB,                                -- {"x0":…,"y0":…,"x1":…,"y1":…,"page_w":…,"page_h":…}
  snippet              TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT field_evidence_owner_ck CHECK ((document_version_id IS NOT NULL) <> (doc_line_id IS NOT NULL))
);
CREATE INDEX field_evidence_version_ix ON field_evidence (document_version_id, field_name) WHERE document_version_id IS NOT NULL;
CREATE INDEX field_evidence_line_ix    ON field_evidence (doc_line_id, field_name)         WHERE doc_line_id IS NOT NULL;
""",

    # Backend Schema source line 714
    r"""CREATE TABLE document_warning (
  id                   UUID PRIMARY KEY,
  workspace_id         UUID NOT NULL REFERENCES workspace(id),
  document_id          UUID REFERENCES document(id),
  document_version_id  UUID REFERENCES document_version(id),
  source_file_id       UUID REFERENCES source_file(id),
  code                 TEXT NOT NULL,                        -- see the code list below
  level                warn_level NOT NULL DEFAULT 'warning',
  message              TEXT NOT NULL,
  detail               JSONB,
  resolved_at          TIMESTAMPTZ,
  resolved_by          UUID REFERENCES app_user(id),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT document_warning_target_ck CHECK (document_id IS NOT NULL OR source_file_id IS NOT NULL)
);
CREATE INDEX document_warning_doc_ix  ON document_warning (document_id);
CREATE INDEX document_warning_file_ix ON document_warning (source_file_id);
CREATE INDEX document_warning_open_ix ON document_warning (workspace_id, code, created_at DESC) WHERE resolved_at IS NULL;
""",
)


def upgrade() -> None:
    for sql in SQL_BLOCKS:
        execute_sql_block(sql)


def downgrade() -> None:
    raise NotImplementedError("ReconBench migrations are fixed-forward; redeploy the prior image")
