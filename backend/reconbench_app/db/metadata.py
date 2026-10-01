"""Static SQLAlchemy 2.0 metadata for the validated ReconBench schema.

This is a checked-in schema declaration, not runtime database reflection. It covers
the 41 application tables from Backend Schema sections 3–8. Alembic's own
alembic_version table and the separately installed Procrastinate schema are not
application metadata. Catalog-only objects are checked by check_schema_contract.py.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from reconbench_app.db.numeric_types import D_AMOUNT, D_QTY, D_SCORE, D_TAXRATE

metadata = sa.MetaData()
_tables: dict[str, sa.Table] = {}

_tables["vendor_merge_suggestion"] = sa.Table(
    "vendor_merge_suggestion",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("vendor_a_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("vendor_b_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("score", D_SCORE, nullable=False),
    sa.Column(
        "state",
        postgresql.ENUM(
            "pending", "accepted", "rejected", name="suggestion_state", create_type=False
        ),
        server_default=sa.text("'pending'::suggestion_state"),
        nullable=False,
    ),
    sa.Column(
        "ai_opinion",
        postgresql.ENUM("same", "different", "unsure", name="ai_opinion", create_type=False),
    ),
    sa.Column("ai_reason", sa.Text()),
    sa.Column("survivor_vendor_id", postgresql.UUID(as_uuid=True)),
    sa.Column("decided_by", postgresql.UUID(as_uuid=True)),
    sa.Column("decided_at", sa.TIMESTAMP(timezone=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.Column("ai_call_id", postgresql.UUID(as_uuid=True)),
    sa.PrimaryKeyConstraint("id", name="vendor_merge_suggestion_pkey"),
    sa.ForeignKeyConstraint(
        ["ai_call_id"], ["ai_call_log.id"], name="vendor_merge_suggestion_ai_call_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["decided_by"], ["app_user.id"], name="vendor_merge_suggestion_decided_by_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["survivor_vendor_id"],
        ["vendor.id"],
        name="vendor_merge_suggestion_survivor_vendor_id_fkey",
    ),
    sa.ForeignKeyConstraint(
        ["vendor_a_id"], ["vendor.id"], name="vendor_merge_suggestion_vendor_a_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["vendor_b_id"], ["vendor.id"], name="vendor_merge_suggestion_vendor_b_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="vendor_merge_suggestion_workspace_id_fkey"
    ),
    sa.CheckConstraint("vendor_a_id < vendor_b_id", name="vms_pair_order"),
)

_tables["vendor"] = sa.Table(
    "vendor",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("canonical_name", sa.Text(), nullable=False),
    sa.Column("normalized_name", sa.Text(), nullable=False),
    sa.Column("pan", sa.Text()),
    sa.Column(
        "anchor",
        postgresql.ENUM("gstin", "pan", "name", name="vendor_anchor", create_type=False),
        nullable=False,
    ),
    sa.Column(
        "created_by_rule",
        postgresql.ENUM(
            "gstin",
            "pan",
            "name_exact",
            "alias",
            "fuzzy",
            "manual",
            "none",
            name="vendor_rule",
            create_type=False,
        ),
    ),
    sa.Column("merged_into", postgresql.UUID(as_uuid=True)),
    sa.Column("merged_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("merged_by", postgresql.UUID(as_uuid=True)),
    sa.Column("input_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="vendor_pkey"),
    sa.ForeignKeyConstraint(["merged_by"], ["app_user.id"], name="vendor_merged_by_fkey"),
    sa.ForeignKeyConstraint(["merged_into"], ["vendor.id"], name="vendor_merged_into_fkey"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="vendor_workspace_id_fkey"),
    sa.CheckConstraint("merged_into IS NULL OR merged_into <> id", name="vendor_no_self_merge"),
)

_tables["workspace"] = sa.Table(
    "workspace",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="workspace_pkey"),
)

_tables["vendor_alias"] = sa.Table(
    "vendor_alias",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("alias_raw", sa.Text(), nullable=False),
    sa.Column("alias_norm", sa.Text(), nullable=False),
    sa.Column(
        "source",
        postgresql.ENUM(
            "merge", "manual", "learned", "seed", name="alias_source", create_type=False
        ),
        nullable=False,
    ),
    sa.Column("suggestion_id", postgresql.UUID(as_uuid=True)),
    sa.Column("created_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="vendor_alias_pkey"),
    sa.UniqueConstraint(
        "workspace_id", "alias_norm", name="vendor_alias_workspace_id_alias_norm_key"
    ),
    sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], name="vendor_alias_created_by_fkey"),
    sa.ForeignKeyConstraint(
        ["suggestion_id"], ["vendor_merge_suggestion.id"], name="vendor_alias_suggestion_id_fkey"
    ),
    sa.ForeignKeyConstraint(["vendor_id"], ["vendor.id"], name="vendor_alias_vendor_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="vendor_alias_workspace_id_fkey"
    ),
)

_tables["batch"] = sa.Table(
    "batch",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("label", sa.Text()),
    sa.Column("label_is_suggested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("period_start", sa.Date()),
    sa.Column("period_end", sa.Date()),
    sa.Column("created_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="batch_pkey"),
    sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], name="batch_created_by_fkey"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="batch_workspace_id_fkey"),
    sa.CheckConstraint(
        "period_start IS NULL OR period_end IS NULL OR period_end >= period_start",
        name="batch_period_ck",
    ),
)

_tables["layout_profile"] = sa.Table(
    "layout_profile",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("signature", sa.Text(), nullable=False),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column("vendor_id", postgresql.UUID(as_uuid=True)),
    sa.Column("body", postgresql.JSONB(), nullable=False),
    sa.Column("created_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="layout_profile_pkey"),
    sa.UniqueConstraint(
        "workspace_id",
        "signature",
        "kind",
        "vendor_id",
        name="layout_profile_workspace_id_signature_kind_vendor_id_key",
        postgresql_nulls_not_distinct=True,
    ),
    sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], name="layout_profile_created_by_fkey"),
    sa.ForeignKeyConstraint(["vendor_id"], ["vendor.id"], name="layout_profile_vendor_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="layout_profile_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "kind = ANY (ARRAY['xml_profile'::text, 'csv_mapping'::text, 'pdf_labels'::text])",
        name="layout_profile_kind_check",
    ),
)

_tables["doc_line"] = sa.Table(
    "doc_line",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("line_no", sa.Integer(), nullable=False),
    sa.Column(
        "line_type",
        postgresql.ENUM("goods", "charge", name="line_type", create_type=False),
        server_default=sa.text("'goods'::line_type"),
        nullable=False,
    ),
    sa.Column("description", sa.Text()),
    sa.Column("description_norm", sa.Text()),
    sa.Column(
        "tokens",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column(
        "spec_tokens",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column("item_code", sa.Text()),
    sa.Column("hsn_sac", sa.Text()),
    sa.Column("uom", sa.Text()),
    sa.Column("uom_as_read", sa.Text()),
    sa.Column("quantity", D_QTY),
    sa.Column("rate", D_QTY),
    sa.Column("discount", D_AMOUNT),
    sa.Column("amount", D_AMOUNT),
    sa.Column("tax_rate", D_TAXRATE),
    sa.Column("cgst_amount", D_AMOUNT),
    sa.Column("sgst_amount", D_AMOUNT),
    sa.Column("igst_amount", D_AMOUNT),
    sa.Column("po_ref", sa.Text()),
    sa.Column("canonical_po_ref", sa.Text()),
    sa.Column("po_line_ref", sa.Text()),
    sa.Column("received_qty", D_QTY),
    sa.Column("rejected_qty", D_QTY),
    sa.Column("accepted_qty", D_QTY),
    sa.Column("extras", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="doc_line_pkey"),
    sa.UniqueConstraint(
        "document_version_id", "line_no", name="doc_line_document_version_id_line_no_key"
    ),
    sa.ForeignKeyConstraint(
        ["document_version_id"], ["document_version.id"], name="doc_line_document_version_id_fkey"
    ),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="doc_line_workspace_id_fkey"),
)

_tables["field_evidence"] = sa.Table(
    "field_evidence",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
    sa.Column("doc_line_id", postgresql.UUID(as_uuid=True)),
    sa.Column("field_name", sa.Text(), nullable=False),
    sa.Column("value_text", sa.Text()),
    sa.Column("confidence", D_SCORE, nullable=False),
    sa.Column(
        "source",
        postgresql.ENUM("rule", "ai", "manual", name="field_source", create_type=False),
        nullable=False,
    ),
    sa.Column("page", sa.Integer()),
    sa.Column("bbox", postgresql.JSONB()),
    sa.Column("snippet", sa.Text()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("ai_call_id", postgresql.UUID(as_uuid=True)),
    sa.PrimaryKeyConstraint("id", name="field_evidence_pkey"),
    sa.ForeignKeyConstraint(
        ["ai_call_id"], ["ai_call_log.id"], name="field_evidence_ai_call_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["doc_line_id"], ["doc_line.id"], name="field_evidence_doc_line_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["document_version_id"],
        ["document_version.id"],
        name="field_evidence_document_version_id_fkey",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="field_evidence_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "(document_version_id IS NOT NULL) <> (doc_line_id IS NOT NULL)",
        name="field_evidence_owner_ck",
    ),
)

_tables["link_current"] = sa.Table(
    "link_current",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "link_type",
        postgresql.ENUM("inv_po", "grn_po", "inv_grn", name="link_type", create_type=False),
        nullable=False,
    ),
    sa.Column("from_document_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("from_line_no", sa.Integer()),
    sa.Column("to_document_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("current_link_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="link_current_pkey"),
    sa.UniqueConstraint(
        "workspace_id",
        "link_type",
        "from_document_id",
        "from_line_no",
        "to_document_id",
        name="link_current_workspace_id_link_type_from_document_id_from_l_key",
        postgresql_nulls_not_distinct=True,
    ),
    sa.ForeignKeyConstraint(
        ["current_link_id"], ["link.id"], name="link_current_current_link_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["from_document_id"], ["document.id"], name="link_current_from_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["to_document_id"], ["document.id"], name="link_current_to_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="link_current_workspace_id_fkey"
    ),
)

_tables["link"] = sa.Table(
    "link",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "link_type",
        postgresql.ENUM("inv_po", "grn_po", "inv_grn", name="link_type", create_type=False),
        nullable=False,
    ),
    sa.Column(
        "level",
        postgresql.ENUM("header", "line", name="link_level", create_type=False),
        nullable=False,
    ),
    sa.Column("from_document_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("from_line_no", sa.Integer()),
    sa.Column("to_document_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("version_no", sa.Integer(), nullable=False),
    sa.Column("supersedes_link_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "method",
        postgresql.ENUM(
            "labeled",
            "reverse",
            "via_grn",
            "content",
            "manual",
            name="link_method",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("confidence", D_SCORE, nullable=False),
    sa.Column(
        "state",
        postgresql.ENUM(
            "auto", "suggested", "confirmed", "rejected", name="link_state", create_type=False
        ),
        nullable=False,
    ),
    sa.Column("ai_suggested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("evidence_snippet", sa.Text()),
    sa.Column("evidence_page", sa.Integer()),
    sa.Column("evidence", postgresql.JSONB()),
    sa.Column("detail_code", sa.Text()),
    sa.Column("created_by", postgresql.UUID(as_uuid=True)),
    sa.Column("confirmed_by", postgresql.UUID(as_uuid=True)),
    sa.Column("confirmed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("ai_call_id", postgresql.UUID(as_uuid=True)),
    sa.PrimaryKeyConstraint("id", name="link_pkey"),
    sa.UniqueConstraint(
        "workspace_id",
        "link_type",
        "from_document_id",
        "from_line_no",
        "to_document_id",
        "version_no",
        name="link_workspace_id_link_type_from_document_id_from_line_no_t_key",
        postgresql_nulls_not_distinct=True,
    ),
    sa.ForeignKeyConstraint(["ai_call_id"], ["ai_call_log.id"], name="link_ai_call_id_fkey"),
    sa.ForeignKeyConstraint(["confirmed_by"], ["app_user.id"], name="link_confirmed_by_fkey"),
    sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], name="link_created_by_fkey"),
    sa.ForeignKeyConstraint(
        ["from_document_id"], ["document.id"], name="link_from_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["supersedes_link_id"], ["link.id"], name="link_supersedes_link_id_fkey"
    ),
    sa.ForeignKeyConstraint(["to_document_id"], ["document.id"], name="link_to_document_id_fkey"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="link_workspace_id_fkey"),
    sa.CheckConstraint(
        "state <> 'confirmed'::link_state OR confirmed_by IS NOT NULL", name="link_confirmed_ck"
    ),
    sa.CheckConstraint(
        "(level = 'line'::link_level) = (from_line_no IS NOT NULL)", name="link_level_ck"
    ),
    sa.CheckConstraint(
        "method <> 'manual'::link_method OR confidence::numeric = 1::numeric", name="link_manual_ck"
    ),
    sa.CheckConstraint("from_document_id <> to_document_id", name="link_not_self_ck"),
)

_tables["link_resolution"] = sa.Table(
    "link_resolution",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "outcome",
        postgresql.ENUM(
            "linked", "suggested", "unlinked", name="resolution_outcome", create_type=False
        ),
        nullable=False,
    ),
    sa.Column("resolved_step", sa.SmallInteger()),
    sa.Column("detail_code", sa.Text()),
    sa.Column(
        "cited_refs",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column(
        "candidates", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
    ),
    sa.Column(
        "input_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column(
        "computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="link_resolution_pkey"),
    sa.UniqueConstraint(
        "workspace_id", "document_id", name="link_resolution_workspace_id_document_id_key"
    ),
    sa.ForeignKeyConstraint(
        ["document_id"], ["document.id"], name="link_resolution_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="link_resolution_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "resolved_step >= 0 AND resolved_step <= 4", name="link_resolution_resolved_step_check"
    ),
)

_tables["opening_balance"] = sa.Table(
    "opening_balance",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("source_file_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("row_no", sa.Integer(), nullable=False),
    sa.Column("vendor_id", postgresql.UUID(as_uuid=True)),
    sa.Column("po_no_raw", sa.Text(), nullable=False),
    sa.Column("canonical_po_no", sa.Text(), nullable=False),
    sa.Column("po_line_no", sa.Integer(), nullable=False),
    sa.Column("po_document_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "received_qty",
        D_QTY,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column(
        "billed_qty",
        D_QTY,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column("as_of_date", sa.Date(), nullable=False),
    sa.Column("withdrawn_at", sa.TIMESTAMP(timezone=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="opening_balance_pkey"),
    sa.UniqueConstraint(
        "source_file_id", "row_no", name="opening_balance_source_file_id_row_no_key"
    ),
    sa.ForeignKeyConstraint(
        ["po_document_id"], ["document.id"], name="opening_balance_po_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["source_file_id"], ["source_file.id"], name="opening_balance_source_file_id_fkey"
    ),
    sa.ForeignKeyConstraint(["vendor_id"], ["vendor.id"], name="opening_balance_vendor_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="opening_balance_workspace_id_fkey"
    ),
)

_tables["ai_call_log"] = sa.Table(
    "ai_call_log",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("run_id", postgresql.UUID(as_uuid=True)),
    sa.Column("document_id", postgresql.UUID(as_uuid=True)),
    sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "hook",
        postgresql.ENUM(
            "h1_extract",
            "h2_item",
            "h3_rerank",
            "h4_vendor",
            "h5_explain",
            name="ai_hook",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column(
        "provider",
        postgresql.ENUM("claude", "openai", "gemini", name="ai_provider", create_type=False),
        nullable=False,
    ),
    sa.Column("model", sa.Text(), nullable=False),
    sa.Column("prompt_version", sa.Text(), nullable=False),
    sa.Column(
        "input_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column(
        "outcome",
        postgresql.ENUM(
            "ok",
            "cache_hit",
            "invalid_output",
            "ungrounded",
            "timeout",
            "rate_limited",
            "bad_key",
            "budget_stopped",
            "error",
            name="ai_outcome",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("error_code", sa.Text()),
    sa.Column("redacted", sa.Boolean(), nullable=False),
    sa.Column("tokens_in", sa.Integer()),
    sa.Column("tokens_out", sa.Integer()),
    sa.Column("cost_usd", sa.Numeric(precision=12, scale=6)),
    sa.Column("latency_ms", sa.Integer()),
    sa.Column("job_id", sa.BigInteger()),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="ai_call_log_pkey"),
    sa.ForeignKeyConstraint(["document_id"], ["document.id"], name="ai_call_log_document_id_fkey"),
    sa.ForeignKeyConstraint(
        ["document_version_id"],
        ["document_version.id"],
        name="ai_call_log_document_version_id_fkey",
    ),
    sa.ForeignKeyConstraint(["run_id"], ["recon_run.id"], name="ai_call_log_run_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="ai_call_log_workspace_id_fkey"
    ),
)

_tables["export_file"] = sa.Table(
    "export_file",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "included_run_ids",
        postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
        server_default=sa.text("'{}'::uuid[]"),
        nullable=False,
    ),
    sa.Column("filter", postgresql.JSONB(), nullable=False),
    sa.Column(
        "filter_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column("scope", sa.Text(), nullable=False),
    sa.Column("as_of", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column(
        "status",
        postgresql.ENUM(
            "queued",
            "building",
            "ready",
            "failed",
            "expired",
            name="export_status",
            create_type=False,
        ),
        server_default=sa.text("'queued'::export_status"),
        nullable=False,
    ),
    sa.Column(
        "sheets",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column("case_count", sa.Integer()),
    sa.Column("object_key", sa.Text()),
    sa.Column("size_bytes", sa.BigInteger()),
    sa.Column("error_code", sa.Text()),
    sa.Column("error_message", sa.Text()),
    sa.Column("job_id", sa.BigInteger()),
    sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("built_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_downloaded_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("download_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="export_file_pkey"),
    sa.ForeignKeyConstraint(
        ["requested_by"], ["app_user.id"], name="export_file_requested_by_fkey"
    ),
    sa.ForeignKeyConstraint(["run_id"], ["recon_run.id"], name="export_file_run_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="export_file_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "scope = ANY (ARRAY['filtered'::text, 'all'::text])", name="export_file_scope_check"
    ),
    sa.CheckConstraint(
        "status <> 'ready'::export_status OR object_key IS NOT NULL AND expires_at IS NOT NULL",
        name="export_ready_ck",
    ),
)

_tables["ai_setting"] = sa.Table(
    "ai_setting",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "mode",
        postgresql.ENUM("rules_only", "ai_assisted", "ai_first", name="ai_mode", create_type=False),
        server_default=sa.text("'rules_only'::ai_mode"),
        nullable=False,
    ),
    sa.Column(
        "provider",
        postgresql.ENUM("claude", "openai", "gemini", name="ai_provider", create_type=False),
    ),
    sa.Column("model", sa.Text()),
    sa.Column("budget_cap_usd", sa.Numeric(precision=12, scale=4)),
    sa.Column(
        "key_storage",
        postgresql.ENUM("session", "saved", name="ai_key_storage", create_type=False),
        server_default=sa.text("'session'::ai_key_storage"),
        nullable=False,
    ),
    sa.Column("key_secret_id", postgresql.UUID(as_uuid=True)),
    sa.Column("consent_given", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("consent_by", postgresql.UUID(as_uuid=True)),
    sa.Column("consent_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("consent_text_version", sa.Text()),
    sa.Column("redaction_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    sa.Column(
        "auto_accept_ai_links", sa.Boolean(), server_default=sa.text("false"), nullable=False
    ),
    sa.Column("auto_accept_threshold", D_SCORE),
    sa.Column(
        "status_code", sa.Text(), server_default=sa.text("'RULES_ONLY'::text"), nullable=False
    ),
    sa.Column("last_test_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_test_ok", sa.Boolean()),
    sa.Column("updated_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="ai_setting_pkey"),
    sa.UniqueConstraint("workspace_id", name="ai_setting_workspace_id_key"),
    sa.ForeignKeyConstraint(["consent_by"], ["app_user.id"], name="ai_setting_consent_by_fkey"),
    sa.ForeignKeyConstraint(["key_secret_id"], ["secret.id"], name="ai_setting_key_secret_id_fkey"),
    sa.ForeignKeyConstraint(["updated_by"], ["app_user.id"], name="ai_setting_updated_by_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="ai_setting_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "NOT auto_accept_ai_links OR auto_accept_threshold IS NOT NULL", name="ai_setting_auto_ck"
    ),
    sa.CheckConstraint(
        "budget_cap_usd IS NULL OR budget_cap_usd >= 0::numeric",
        name="ai_setting_budget_cap_usd_check",
    ),
    sa.CheckConstraint(
        "mode = 'rules_only'::ai_mode OR consent_given AND provider IS NOT NULL AND model IS NOT NULL",  # noqa: E501
        name="ai_setting_mode_ck",
    ),
)

_tables["dirty_scope"] = sa.Table(
    "dirty_scope",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "scope_kind",
        postgresql.ENUM("vendor", "po", "invoice", name="scope_type", create_type=False),
        nullable=False,
    ),
    sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("mark_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.Column(
        "first_marked_at",
        sa.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    ),
    sa.Column(
        "last_marked_at",
        sa.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    ),
    sa.Column("claimed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("processed_at", sa.TIMESTAMP(timezone=True)),
    sa.PrimaryKeyConstraint("id", name="dirty_scope_pkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="dirty_scope_workspace_id_fkey"
    ),
)

_tables["processing_event"] = sa.Table(
    "processing_event",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("source_file_id", postgresql.UUID(as_uuid=True)),
    sa.Column("document_id", postgresql.UUID(as_uuid=True)),
    sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "stage",
        postgresql.ENUM(
            "received",
            "sniffed",
            "parsed",
            "qa",
            "gated",
            "upserted",
            "vendor_resolved",
            "linked",
            "ledger_updated",
            "evaluated",
            name="processing_stage",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column(
        "outcome",
        postgresql.ENUM("ok", "warn", "error", "skipped", name="stage_outcome", create_type=False),
        nullable=False,
    ),
    sa.Column("duration_ms", sa.Integer()),
    sa.Column("parser_version", sa.Text()),
    sa.Column(
        "warning_codes",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column("error_code", sa.Text()),
    sa.Column("job_id", sa.BigInteger()),
    sa.Column("detail", postgresql.JSONB()),
    sa.Column(
        "occurred_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="processing_event_pkey"),
    sa.ForeignKeyConstraint(
        ["document_id"], ["document.id"], name="processing_event_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["document_version_id"],
        ["document_version.id"],
        name="processing_event_document_version_id_fkey",
    ),
    sa.ForeignKeyConstraint(
        ["source_file_id"], ["source_file.id"], name="processing_event_source_file_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="processing_event_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "source_file_id IS NOT NULL OR document_id IS NOT NULL", name="processing_event_target_ck"
    ),
)

_tables["idempotency_key"] = sa.Table(
    "idempotency_key",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("idem_key", sa.Text(), nullable=False),
    sa.Column("endpoint", sa.Text(), nullable=False),
    sa.Column(
        "request_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column("response_status", sa.Integer()),
    sa.Column("response_body", postgresql.JSONB()),
    sa.Column("resource_type", sa.Text()),
    sa.Column("resource_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("id", name="idempotency_key_pkey"),
    sa.UniqueConstraint(
        "workspace_id",
        "user_id",
        "endpoint",
        "idem_key",
        name="idempotency_key_workspace_id_user_id_endpoint_idem_key_key",
    ),
    sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], name="idempotency_key_user_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="idempotency_key_workspace_id_fkey"
    ),
)

_tables["vendor_gstin"] = sa.Table(
    "vendor_gstin",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("gstin", sa.Text(), nullable=False),
    sa.Column("state_code", sa.Text(), nullable=False),
    sa.Column("pan", sa.Text(), nullable=False),
    sa.Column("checksum_ok", sa.Boolean(), nullable=False),
    sa.Column(
        "first_seen_at",
        sa.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    ),
    sa.PrimaryKeyConstraint("id", name="vendor_gstin_pkey"),
    sa.UniqueConstraint("workspace_id", "gstin", name="vendor_gstin_workspace_id_gstin_key"),
    sa.ForeignKeyConstraint(["vendor_id"], ["vendor.id"], name="vendor_gstin_vendor_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="vendor_gstin_workspace_id_fkey"
    ),
)

_tables["document"] = sa.Table(
    "document",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "doc_type",
        postgresql.ENUM("po", "grn", "invoice", name="doc_type", create_type=False),
        nullable=False,
    ),
    sa.Column(
        "natural_key",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column("batch_id", postgresql.UUID(as_uuid=True)),
    sa.Column("current_version_id", postgresql.UUID(as_uuid=True)),
    sa.Column("vendor_identity", sa.Text()),
    sa.Column("vendor_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "vendor_rule",
        postgresql.ENUM(
            "gstin",
            "pan",
            "name_exact",
            "alias",
            "fuzzy",
            "manual",
            "none",
            name="vendor_rule",
            create_type=False,
        ),
        server_default=sa.text("'none'::vendor_rule"),
        nullable=False,
    ),
    sa.Column("vendor_score", D_SCORE),
    sa.Column("vendor_locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("vendor_resolved_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("doc_no", sa.Text()),
    sa.Column("canonical_doc_no", sa.Text()),
    sa.Column("doc_date", sa.Date()),
    sa.Column("grand_total", D_AMOUNT),
    sa.Column("needs_verification", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("withdrawn_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("withdrawn_by", postgresql.UUID(as_uuid=True)),
    sa.Column("input_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    sa.Column(
        "first_seen_at",
        sa.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="document_pkey"),
    sa.ForeignKeyConstraint(["batch_id"], ["batch.id"], name="document_batch_id_fkey"),
    sa.ForeignKeyConstraint(
        ["current_version_id"],
        ["document_version.id"],
        name="document_current_version_fk",
        deferrable=True,
        initially="DEFERRED",
    ),
    sa.ForeignKeyConstraint(["vendor_id"], ["vendor.id"], name="document_vendor_id_fkey"),
    sa.ForeignKeyConstraint(["withdrawn_by"], ["app_user.id"], name="document_withdrawn_by_fkey"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="document_workspace_id_fkey"),
)

_tables["document_warning"] = sa.Table(
    "document_warning",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("document_id", postgresql.UUID(as_uuid=True)),
    sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
    sa.Column("source_file_id", postgresql.UUID(as_uuid=True)),
    sa.Column("code", sa.Text(), nullable=False),
    sa.Column(
        "level",
        postgresql.ENUM("info", "warning", "error", name="warn_level", create_type=False),
        server_default=sa.text("'warning'::warn_level"),
        nullable=False,
    ),
    sa.Column("message", sa.Text(), nullable=False),
    sa.Column("detail", postgresql.JSONB()),
    sa.Column("resolved_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("resolved_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="document_warning_pkey"),
    sa.ForeignKeyConstraint(
        ["document_id"], ["document.id"], name="document_warning_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["document_version_id"],
        ["document_version.id"],
        name="document_warning_document_version_id_fkey",
    ),
    sa.ForeignKeyConstraint(
        ["resolved_by"], ["app_user.id"], name="document_warning_resolved_by_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["source_file_id"], ["source_file.id"], name="document_warning_source_file_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="document_warning_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "document_id IS NOT NULL OR source_file_id IS NOT NULL", name="document_warning_target_ck"
    ),
)

_tables["config_active"] = sa.Table(
    "config_active",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("config_version_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("activated_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "activated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="config_active_pkey"),
    sa.UniqueConstraint("workspace_id", name="config_active_workspace_id_key"),
    sa.ForeignKeyConstraint(
        ["activated_by"], ["app_user.id"], name="config_active_activated_by_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["config_version_id"], ["config_version.id"], name="config_active_config_version_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="config_active_workspace_id_fkey"
    ),
)

_tables["po_line_ledger"] = sa.Table(
    "po_line_ledger",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("po_document_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("po_version_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("po_line_no", sa.Integer(), nullable=False),
    sa.Column("po_doc_line_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("uom", sa.Text()),
    sa.Column("po_rate", D_QTY),
    sa.Column("ordered", D_QTY, nullable=False),
    sa.Column(
        "received_accepted",
        D_QTY,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column(
        "received_rejected",
        D_QTY,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column(
        "billed",
        D_QTY,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column(
        "open_qty",
        sa.Numeric(precision=18, scale=4),
        sa.Computed("((ordered)::numeric - (billed)::numeric)", persisted=True),
    ),
    sa.Column(
        "grni_qty",
        sa.Numeric(precision=18, scale=4),
        sa.Computed(
            "GREATEST(((received_accepted)::numeric - (billed)::numeric), (0)::numeric)",
            persisted=True,
        ),
    ),
    sa.Column(
        "completeness_warning", sa.Boolean(), server_default=sa.text("false"), nullable=False
    ),
    sa.Column(
        "input_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column(
        "computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="po_line_ledger_pkey"),
    sa.UniqueConstraint(
        "workspace_id",
        "po_document_id",
        "po_line_no",
        name="po_line_ledger_workspace_id_po_document_id_po_line_no_key",
    ),
    sa.ForeignKeyConstraint(
        ["po_doc_line_id"], ["doc_line.id"], name="po_line_ledger_po_doc_line_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["po_document_id"], ["document.id"], name="po_line_ledger_po_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["po_version_id"], ["document_version.id"], name="po_line_ledger_po_version_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="po_line_ledger_workspace_id_fkey"
    ),
)

_tables["ledger_entry"] = sa.Table(
    "ledger_entry",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("po_line_ledger_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column("seq", sa.Integer(), nullable=False),
    sa.Column("source_document_id", postgresql.UUID(as_uuid=True)),
    sa.Column("source_doc_line_id", postgresql.UUID(as_uuid=True)),
    sa.Column("opening_balance_id", postgresql.UUID(as_uuid=True)),
    sa.Column("sort_date", sa.Date()),
    sa.Column("sort_no", sa.Text()),
    sa.Column(
        "sort_key", postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False)
    ),
    sa.Column(
        "accepted_delta",
        D_QTY,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column(
        "rejected_delta",
        D_QTY,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column(
        "billed_delta",
        D_QTY,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column("consumes", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    sa.Column("excluded_reason", sa.Text()),
    sa.Column("alignment_score", D_SCORE),
    sa.Column(
        "alignment_state",
        postgresql.ENUM(
            "aligned",
            "unsure",
            "not_on_po",
            "charge",
            "unlinked",
            name="alignment_state",
            create_type=False,
        ),
    ),
    sa.PrimaryKeyConstraint("id", name="ledger_entry_pkey"),
    sa.UniqueConstraint("po_line_ledger_id", "seq", name="ledger_entry_po_line_ledger_id_seq_key"),
    sa.ForeignKeyConstraint(
        ["opening_balance_id"], ["opening_balance.id"], name="ledger_entry_opening_balance_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["po_line_ledger_id"],
        ["po_line_ledger.id"],
        name="ledger_entry_po_line_ledger_id_fkey",
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["source_doc_line_id"], ["doc_line.id"], name="ledger_entry_source_doc_line_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["source_document_id"], ["document.id"], name="ledger_entry_source_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="ledger_entry_workspace_id_fkey"
    ),
    sa.CheckConstraint("consumes = (excluded_reason IS NULL)", name="ledger_entry_consumes_ck"),
    sa.CheckConstraint(
        "excluded_reason = ANY (ARRAY['duplicate'::text, 'rejected'::text])",
        name="ledger_entry_excluded_reason_check",
    ),
    sa.CheckConstraint(
        "kind = ANY (ARRAY['opening'::text, 'grn'::text, 'invoice'::text])",
        name="ledger_entry_kind_check",
    ),
    sa.CheckConstraint(
        "kind = 'opening'::text AND opening_balance_id IS NOT NULL AND source_doc_line_id IS NULL OR (kind = ANY (ARRAY['grn'::text, 'invoice'::text])) AND source_doc_line_id IS NOT NULL AND opening_balance_id IS NULL",  # noqa: E501
        name="ledger_entry_source_ck",
    ),
)

_tables["reason_code"] = sa.Table(
    "reason_code",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("code", sa.Text(), nullable=False),
    sa.Column(
        "level",
        postgresql.ENUM(
            "block",
            "review",
            "fail",
            "pending",
            "warn",
            "info",
            name="reason_level",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("stage", sa.Text(), nullable=False),
    sa.Column("title", sa.Text(), nullable=False),
    sa.Column("message", sa.Text(), nullable=False),
    sa.Column("suggested_action", sa.Text(), nullable=False),
    sa.Column("overridable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    sa.Column("level_configurable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("is_proposed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="reason_code_pkey"),
    sa.UniqueConstraint("workspace_id", "code", name="reason_code_workspace_id_code_key"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="reason_code_workspace_id_fkey"
    ),
    sa.CheckConstraint("code ~ '^[A-Z][A-Z0-9_]+$'::text", name="reason_code_fmt_ck"),
    sa.CheckConstraint(
        "stage = ANY (ARRAY['S1'::text, 'S2'::text, 'S3'::text, 'S4'::text, 'S5'::text, 'S6'::text, 'S7'::text])",  # noqa: E501
        name="reason_code_stage_check",
    ),
)

_tables["recon_run"] = sa.Table(
    "recon_run",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "status",
        postgresql.ENUM(
            "queued", "running", "succeeded", "failed", name="run_status", create_type=False
        ),
        server_default=sa.text("'queued'::run_status"),
        nullable=False,
    ),
    sa.Column(
        "trigger_kind",
        postgresql.ENUM(
            "user", "auto_pending", "manual_recon", name="run_trigger", create_type=False
        ),
        server_default=sa.text("'user'::run_trigger"),
        nullable=False,
    ),
    sa.Column("scope", postgresql.JSONB(), nullable=False),
    sa.Column(
        "scope_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column("run_date", sa.Date(), nullable=False),
    sa.Column("engine_version", sa.Text(), nullable=False),
    sa.Column(
        "rules_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column("config_version_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "manifest_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
    ),
    sa.Column("replay_of_run_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "ai_mode",
        postgresql.ENUM("rules_only", "ai_assisted", "ai_first", name="ai_mode", create_type=False),
        server_default=sa.text("'rules_only'::ai_mode"),
        nullable=False,
    ),
    sa.Column(
        "ai_provider",
        postgresql.ENUM("claude", "openai", "gemini", name="ai_provider", create_type=False),
    ),
    sa.Column("ai_model", sa.Text()),
    sa.Column("ai_prompt_versions", postgresql.JSONB()),
    sa.Column("ai_consent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("total_cases", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("done_cases", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column(
        "status_counts", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
    ),
    sa.Column("metrics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column("error_code", sa.Text()),
    sa.Column("error_message", sa.Text()),
    sa.Column("job_id", sa.BigInteger()),
    sa.Column("requested_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "queued_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="recon_run_pkey"),
    sa.ForeignKeyConstraint(
        ["config_version_id"], ["config_version.id"], name="recon_run_config_version_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["replay_of_run_id"], ["recon_run.id"], name="recon_run_replay_of_run_id_fkey"
    ),
    sa.ForeignKeyConstraint(["requested_by"], ["app_user.id"], name="recon_run_requested_by_fkey"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="recon_run_workspace_id_fkey"),
    sa.CheckConstraint(
        "ai_mode = 'rules_only'::ai_mode OR ai_consent", name="recon_run_ai_consent_ck"
    ),
    sa.CheckConstraint("done_cases <= total_cases", name="recon_run_progress_ck"),
)

_tables["case_current"] = sa.Table(
    "case_current",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("vendor_id", postgresql.UUID(as_uuid=True)),
    sa.Column("invoice_date", sa.Date()),
    sa.Column(
        "computed_status",
        postgresql.ENUM(
            "duplicate",
            "unlinked",
            "needs_review",
            "exception",
            "pending",
            "matched_flags",
            "matched",
            name="case_status",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column(
        "overlay",
        postgresql.ENUM("approved", "on_hold", "rejected", name="overlay_type", create_type=False),
    ),
    sa.Column("effective_status", sa.Text(), nullable=False),
    sa.Column("severity_rank", sa.SmallInteger(), nullable=False),
    sa.Column("human_touched", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("latest_action_id", postgresql.UUID(as_uuid=True)),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="case_current_pkey"),
    sa.UniqueConstraint(
        "workspace_id", "invoice_id", name="case_current_workspace_id_invoice_id_key"
    ),
    sa.ForeignKeyConstraint(["case_id"], ["recon_case.id"], name="case_current_case_id_fkey"),
    sa.ForeignKeyConstraint(["invoice_id"], ["document.id"], name="case_current_invoice_id_fkey"),
    sa.ForeignKeyConstraint(
        ["latest_action_id"], ["case_action.id"], name="case_current_latest_action_id_fkey"
    ),
    sa.ForeignKeyConstraint(["run_id"], ["recon_run.id"], name="case_current_run_id_fkey"),
    sa.ForeignKeyConstraint(["vendor_id"], ["vendor.id"], name="case_current_vendor_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="case_current_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "effective_status = ANY (ARRAY['duplicate'::text, 'unlinked'::text, 'needs_review'::text, 'exception'::text, 'pending'::text, 'matched_flags'::text, 'matched'::text, 'approved'::text, 'on_hold'::text, 'rejected'::text])",  # noqa: E501
        name="case_current_effective_ck",
    ),
)

_tables["document_version"] = sa.Table(
    "document_version",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("seq", sa.Integer(), nullable=False),
    sa.Column("revision", sa.Text()),
    sa.Column("revision_rank", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column(
        "content_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column("doc_no", sa.Text()),
    sa.Column("canonical_doc_no", sa.Text()),
    sa.Column("doc_date", sa.Date()),
    sa.Column(
        "po_status",
        postgresql.ENUM("open", "closed", "cancelled", name="po_status", create_type=False),
    ),
    sa.Column("vendor_name", sa.Text()),
    sa.Column("vendor_name_norm", sa.Text()),
    sa.Column("vendor_gstin", sa.Text()),
    sa.Column("vendor_pan", sa.Text()),
    sa.Column("vendor_state_code", sa.Text()),
    sa.Column("buyer_gstin", sa.Text()),
    sa.Column("ship_to", sa.Text()),
    sa.Column("place_of_supply", sa.Text()),
    sa.Column("payment_terms_text", sa.Text()),
    sa.Column("payment_terms_days", sa.Integer()),
    sa.Column("delivery_date", sa.Date()),
    sa.Column(
        "currency", sa.CHAR(length=3), server_default=sa.text("'INR'::bpchar"), nullable=False
    ),
    sa.Column("taxable_total", D_AMOUNT),
    sa.Column("cgst_total", D_AMOUNT),
    sa.Column("sgst_total", D_AMOUNT),
    sa.Column("igst_total", D_AMOUNT),
    sa.Column("round_off", D_AMOUNT),
    sa.Column("grand_total", D_AMOUNT),
    sa.Column(
        "po_refs",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column(
        "canonical_po_refs",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column(
        "grn_refs",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column(
        "canonical_grn_refs",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column("challan_no", sa.Text()),
    sa.Column("canonical_challan_no", sa.Text()),
    sa.Column("e_way_bill_no", sa.Text()),
    sa.Column("irn", sa.Text()),
    sa.Column("supplier_invoice_no", sa.Text()),
    sa.Column("canonical_supplier_invoice_no", sa.Text()),
    sa.Column(
        "extraction_method",
        postgresql.ENUM(
            "xml_profile",
            "csv_mapping",
            "pdf_text",
            "pdf_ocr",
            "ai_fallback",
            "ai_first",
            "manual",
            "shell",
            name="extraction_method",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("extraction_conf", D_SCORE),
    sa.Column("needs_verification", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column(
        "verification_reasons",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column("extras", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column("source_file_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("locator", postgresql.JSONB()),
    sa.Column("page_count", sa.Integer()),
    sa.Column("parser_version", sa.Text(), nullable=False),
    sa.Column("layout_signature", sa.Text()),
    sa.Column("derived_from_version_id", postgresql.UUID(as_uuid=True)),
    sa.Column("created_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="document_version_pkey"),
    sa.UniqueConstraint("document_id", "seq", name="document_version_document_id_seq_key"),
    sa.ForeignKeyConstraint(
        ["created_by"], ["app_user.id"], name="document_version_created_by_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["derived_from_version_id"],
        ["document_version.id"],
        name="document_version_derived_from_version_id_fkey",
    ),
    sa.ForeignKeyConstraint(
        ["document_id"], ["document.id"], name="document_version_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["source_file_id"], ["source_file.id"], name="document_version_source_file_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="document_version_workspace_id_fkey"
    ),
)

_tables["config_version"] = sa.Table(
    "config_version",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("seq", sa.Integer(), nullable=False),
    sa.Column("parent_version_id", postgresql.UUID(as_uuid=True)),
    sa.Column("body", postgresql.JSONB(), nullable=False),
    sa.Column(
        "engine_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column(
        "body_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column("change_note", sa.Text()),
    sa.Column("created_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="config_version_pkey"),
    sa.UniqueConstraint("workspace_id", "seq", name="config_version_workspace_id_seq_key"),
    sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], name="config_version_created_by_fkey"),
    sa.ForeignKeyConstraint(
        ["parent_version_id"], ["config_version.id"], name="config_version_parent_version_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="config_version_workspace_id_fkey"
    ),
)

_tables["recon_case"] = sa.Table(
    "recon_case",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("invoice_version_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("vendor_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "status",
        postgresql.ENUM(
            "duplicate",
            "unlinked",
            "needs_review",
            "exception",
            "pending",
            "matched_flags",
            "matched",
            name="case_status",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("aged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("pending_days", sa.Integer()),
    sa.Column("duplicate_of_id", postgresql.UUID(as_uuid=True)),
    sa.Column("invoice_taxable", D_AMOUNT),
    sa.Column("invoice_total", D_AMOUNT),
    sa.Column("expected_taxable", D_AMOUNT),
    sa.Column("variance", D_AMOUNT),
    sa.Column("variance_pct", sa.Numeric(precision=9, scale=4)),
    sa.Column(
        "payable_taxable",
        D_AMOUNT,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column(
        "at_risk",
        D_AMOUNT,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column(
        "at_risk_gst",
        D_AMOUNT,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column(
        "blocked_value",
        D_AMOUNT,
        server_default=sa.text("0"),
        nullable=False,
    ),
    sa.Column("payment_due_date", sa.Date()),
    sa.Column(
        "po_nos",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column(
        "grn_nos",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column(
        "link_summary", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
    ),
    sa.Column(
        "reason_codes",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column("checks", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column("inputs", postgresql.JSONB(), nullable=False),
    sa.Column(
        "input_fingerprint",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column("vendor_seq", sa.BigInteger()),
    sa.Column("po_seqs", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="recon_case_pkey"),
    sa.UniqueConstraint("run_id", "invoice_id", name="recon_case_run_id_invoice_id_key"),
    sa.ForeignKeyConstraint(
        ["duplicate_of_id"], ["document.id"], name="recon_case_duplicate_of_id_fkey"
    ),
    sa.ForeignKeyConstraint(["invoice_id"], ["document.id"], name="recon_case_invoice_id_fkey"),
    sa.ForeignKeyConstraint(
        ["invoice_version_id"], ["document_version.id"], name="recon_case_invoice_version_id_fkey"
    ),
    sa.ForeignKeyConstraint(["run_id"], ["recon_run.id"], name="recon_case_run_id_fkey"),
    sa.ForeignKeyConstraint(["vendor_id"], ["vendor.id"], name="recon_case_vendor_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="recon_case_workspace_id_fkey"
    ),
    sa.CheckConstraint("NOT aged OR status = 'exception'::case_status", name="recon_case_aged_ck"),
    sa.CheckConstraint(
        "(status <> ALL (ARRAY['duplicate'::case_status, 'unlinked'::case_status])) OR at_risk::numeric = 0::numeric",  # noqa: E501
        name="recon_case_atrisk_ck",
    ),
    sa.CheckConstraint(
        "(status = ANY (ARRAY['duplicate'::case_status, 'unlinked'::case_status])) OR blocked_value::numeric = 0::numeric",  # noqa: E501
        name="recon_case_blocked_ck",
    ),
)

_tables["recon_line_result"] = sa.Table(
    "recon_line_result",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("invoice_line_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("line_no", sa.Integer(), nullable=False),
    sa.Column(
        "line_type",
        postgresql.ENUM("goods", "charge", name="line_type", create_type=False),
        nullable=False,
    ),
    sa.Column(
        "status",
        postgresql.ENUM(
            "duplicate",
            "unlinked",
            "needs_review",
            "exception",
            "pending",
            "matched_flags",
            "matched",
            name="case_status",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("po_document_id", postgresql.UUID(as_uuid=True)),
    sa.Column("po_line_no", sa.Integer()),
    sa.Column("po_doc_line_id", postgresql.UUID(as_uuid=True)),
    sa.Column("alignment_score", D_SCORE),
    sa.Column(
        "alignment_state",
        postgresql.ENUM(
            "aligned",
            "unsure",
            "not_on_po",
            "charge",
            "unlinked",
            name="alignment_state",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column(
        "ai_item_opinion",
        postgresql.ENUM("same", "different", "unsure", name="ai_opinion", create_type=False),
    ),
    sa.Column("ai_item_reason", sa.Text()),
    sa.Column(
        "grn_document_ids",
        postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
        server_default=sa.text("'{}'::uuid[]"),
        nullable=False,
    ),
    sa.Column("uom", sa.Text()),
    sa.Column("po_qty", D_QTY),
    sa.Column("po_cap", D_QTY),
    sa.Column("billed_before", D_QTY),
    sa.Column("billed_qty", D_QTY),
    sa.Column("grn_accepted_cum", D_QTY),
    sa.Column("grn_rejected_cum", D_QTY),
    sa.Column("eligible_qty", D_QTY),
    sa.Column("balance_after", D_QTY),
    sa.Column("shortfall_qty", D_QTY),
    sa.Column(
        "provisional_before_count", sa.Integer(), server_default=sa.text("0"), nullable=False
    ),
    sa.Column("po_rate", D_QTY),
    sa.Column("invoice_net_rate", D_QTY),
    sa.Column("rate_var_pct", sa.Numeric(precision=9, scale=4)),
    sa.Column("payable_rate", D_QTY),
    sa.Column("line_taxable", D_AMOUNT),
    sa.Column("payable_taxable", D_AMOUNT),
    sa.Column("at_risk", D_AMOUNT),
    sa.Column(
        "reason_codes",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column("checks", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="recon_line_result_pkey"),
    sa.UniqueConstraint("case_id", "line_no", name="recon_line_result_case_id_line_no_key"),
    sa.ForeignKeyConstraint(["case_id"], ["recon_case.id"], name="recon_line_result_case_id_fkey"),
    sa.ForeignKeyConstraint(
        ["invoice_line_id"], ["doc_line.id"], name="recon_line_result_invoice_line_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["po_doc_line_id"], ["doc_line.id"], name="recon_line_result_po_doc_line_id_fkey"
    ),
    sa.ForeignKeyConstraint(
        ["po_document_id"], ["document.id"], name="recon_line_result_po_document_id_fkey"
    ),
    sa.ForeignKeyConstraint(["run_id"], ["recon_run.id"], name="recon_line_result_run_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="recon_line_result_workspace_id_fkey"
    ),
)

_tables["run_manifest"] = sa.Table(
    "run_manifest",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "manifest_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column("item_count", sa.Integer(), nullable=False),
    sa.Column("items", postgresql.JSONB(), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="run_manifest_pkey"),
    sa.UniqueConstraint("run_id", name="run_manifest_run_id_key"),
    sa.ForeignKeyConstraint(["run_id"], ["recon_run.id"], name="run_manifest_run_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="run_manifest_workspace_id_fkey"
    ),
)

_tables["case_action"] = sa.Table(
    "case_action",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("case_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "action",
        postgresql.ENUM(
            "confirm_link",
            "change_link",
            "approve",
            "hold",
            "reject",
            "note",
            "release",
            name="action_type",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("reason", sa.Text()),
    sa.Column("note", sa.Text()),
    sa.Column("link_id", postgresql.UUID(as_uuid=True)),
    sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "actor_role",
        postgresql.ENUM("ap_executive", "manager", "auditor", name="user_role", create_type=False),
        nullable=False,
    ),
    sa.Column(
        "computed_status",
        postgresql.ENUM(
            "duplicate",
            "unlinked",
            "needs_review",
            "exception",
            "pending",
            "matched_flags",
            "matched",
            name="case_status",
            create_type=False,
        ),
    ),
    sa.Column(
        "reason_codes",
        postgresql.ARRAY(sa.Text()),
        server_default=sa.text("'{}'::text[]"),
        nullable=False,
    ),
    sa.Column(
        "input_fingerprint",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
    ),
    sa.Column("bulk_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "occurred_at",
        sa.TIMESTAMP(timezone=True),
        server_default=sa.text("clock_timestamp()"),
        nullable=False,
    ),
    sa.PrimaryKeyConstraint("id", name="case_action_pkey"),
    sa.ForeignKeyConstraint(["actor_id"], ["app_user.id"], name="case_action_actor_id_fkey"),
    sa.ForeignKeyConstraint(["case_id"], ["recon_case.id"], name="case_action_case_id_fkey"),
    sa.ForeignKeyConstraint(["invoice_id"], ["document.id"], name="case_action_invoice_id_fkey"),
    sa.ForeignKeyConstraint(["link_id"], ["link.id"], name="case_action_link_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="case_action_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "action <> 'approve'::action_type OR length(btrim(COALESCE(reason, ''::text))) > 0",
        name="case_action_approve_ck",
    ),
    sa.CheckConstraint(
        "(action <> ALL (ARRAY['confirm_link'::action_type, 'change_link'::action_type])) OR link_id IS NOT NULL",  # noqa: E501
        name="case_action_link_ck",
    ),
    sa.CheckConstraint(
        "action <> 'note'::action_type OR length(btrim(COALESCE(note, ''::text))) > 0",
        name="case_action_note_ck",
    ),
    sa.CheckConstraint(
        "(action <> ALL (ARRAY['approve'::action_type, 'hold'::action_type, 'reject'::action_type])) OR input_fingerprint IS NOT NULL",  # noqa: E501
        name="case_action_print_ck",
    ),
)

_tables["secret"] = sa.Table(
    "secret",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("purpose", sa.Text(), nullable=False),
    sa.Column("ciphertext", postgresql.BYTEA(), nullable=False),
    sa.Column("nonce", postgresql.BYTEA(), nullable=False),
    sa.Column("key_id", sa.Text(), nullable=False),
    sa.Column("key_hint", sa.Text()),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("created_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("rotated_at", sa.TIMESTAMP(timezone=True)),
    sa.PrimaryKeyConstraint("id", name="secret_pkey"),
    sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], name="secret_created_by_fkey"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="secret_workspace_id_fkey"),
    sa.CheckConstraint("purpose = 'ai_api_key'::text", name="secret_purpose_check"),
)

_tables["session"] = sa.Table(
    "session",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "token_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column(
        "csrf_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "last_seen_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("idle_expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("abs_expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("revoked_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("ip", postgresql.INET()),
    sa.Column("user_agent", sa.Text()),
    sa.PrimaryKeyConstraint("id", name="session_pkey"),
    sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], name="session_user_id_fkey"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="session_workspace_id_fkey"),
)

_tables["rate_limit_event"] = sa.Table(
    "rate_limit_event",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column("user_id", postgresql.UUID(as_uuid=True)),
    sa.Column("email", sa.Text()),
    sa.Column("ip", postgresql.INET()),
    sa.Column("succeeded", sa.Boolean(), nullable=False),
    sa.Column(
        "occurred_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("id", name="rate_limit_event_pkey"),
    sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], name="rate_limit_event_user_id_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="rate_limit_event_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "kind = ANY (ARRAY['login'::text, 'ai_test'::text])", name="rate_limit_event_kind_check"
    ),
)

_tables["app_user"] = sa.Table(
    "app_user",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("email", sa.Text(), nullable=False),
    sa.Column("display_name", sa.Text(), nullable=False),
    sa.Column(
        "role",
        postgresql.ENUM("ap_executive", "manager", "auditor", name="user_role", create_type=False),
        server_default=sa.text("'manager'::user_role"),
        nullable=False,
    ),
    sa.Column("password_hash", sa.Text(), nullable=False),
    sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    sa.Column("failed_logins", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("locked_until", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_login_at", sa.TIMESTAMP(timezone=True)),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="app_user_pkey"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="app_user_workspace_id_fkey"),
    sa.CheckConstraint("email = lower(email)", name="app_user_email_lower"),
)

_tables["document_text"] = sa.Table(
    "document_text",
    metadata,
    sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("page", sa.Integer(), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("char_count", sa.Integer(), nullable=False),
    sa.Column("is_ocr", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.PrimaryKeyConstraint("document_version_id", "page", name="document_text_pkey"),
    sa.ForeignKeyConstraint(
        ["document_version_id"],
        ["document_version.id"],
        name="document_text_document_version_id_fkey",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="document_text_workspace_id_fkey"
    ),
)

_tables["ai_cache"] = sa.Table(
    "ai_cache",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "cache_key",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column(
        "hook",
        postgresql.ENUM(
            "h1_extract",
            "h2_item",
            "h3_rerank",
            "h4_vendor",
            "h5_explain",
            name="ai_hook",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("model", sa.Text(), nullable=False),
    sa.Column("prompt_version", sa.Text(), nullable=False),
    sa.Column(
        "document_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
    ),
    sa.Column("response", postgresql.JSONB(), nullable=False),
    sa.Column("hit_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("last_hit_at", sa.TIMESTAMP(timezone=True)),
    sa.PrimaryKeyConstraint("id", name="ai_cache_pkey"),
    sa.UniqueConstraint("workspace_id", "cache_key", name="ai_cache_workspace_id_cache_key_key"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="ai_cache_workspace_id_fkey"),
)

_tables["source_file"] = sa.Table(
    "source_file",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("batch_id", postgresql.UUID(as_uuid=True)),
    sa.Column("parent_file_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "sha256",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.Column("display_name", sa.Text(), nullable=False),
    sa.Column("archive_path", sa.Text()),
    sa.Column(
        "zone",
        postgresql.ENUM(
            "po", "grn", "invoice", "opening_balance", name="upload_zone", create_type=False
        ),
        nullable=False,
    ),
    sa.Column(
        "zone_uploaded",
        postgresql.ENUM(
            "po", "grn", "invoice", "opening_balance", name="upload_zone", create_type=False
        ),
        nullable=False,
    ),
    sa.Column(
        "format",
        postgresql.ENUM("xml", "csv", "pdf", "zip", name="file_format", create_type=False),
        nullable=False,
    ),
    sa.Column(
        "sniffed_type", postgresql.ENUM("po", "grn", "invoice", name="doc_type", create_type=False)
    ),
    sa.Column(
        "mismatch_state",
        postgresql.ENUM(
            "none", "suggested", "accepted", "kept", name="mismatch_state", create_type=False
        ),
        server_default=sa.text("'none'::mismatch_state"),
        nullable=False,
    ),
    sa.Column(
        "status",
        postgresql.ENUM(
            "staged",
            "queued",
            "parsing",
            "parsed",
            "partial",
            "failed",
            "needs_password",
            "removed",
            name="file_status",
            create_type=False,
        ),
        server_default=sa.text("'staged'::file_status"),
        nullable=False,
    ),
    sa.Column("error_code", sa.Text()),
    sa.Column("error_message", sa.Text()),
    sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("size_bytes", sa.BigInteger(), nullable=False),
    sa.Column("page_count", sa.Integer()),
    sa.Column("object_key", sa.Text(), nullable=False),
    sa.Column("layout_signature", sa.Text()),
    sa.Column("parser_version", sa.Text()),
    sa.Column("docs_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("docs_new", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("docs_updated", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("docs_skipped", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("reupload_skips", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("uploaded_by", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "uploaded_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column("queued_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("parse_started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("parse_finished_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("removed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("removed_by", postgresql.UUID(as_uuid=True)),
    sa.Column("object_purged_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    sa.PrimaryKeyConstraint("id", name="source_file_pkey"),
    sa.ForeignKeyConstraint(["batch_id"], ["batch.id"], name="source_file_batch_id_fkey"),
    sa.ForeignKeyConstraint(
        ["parent_file_id"], ["source_file.id"], name="source_file_parent_file_id_fkey"
    ),
    sa.ForeignKeyConstraint(["removed_by"], ["app_user.id"], name="source_file_removed_by_fkey"),
    sa.ForeignKeyConstraint(["uploaded_by"], ["app_user.id"], name="source_file_uploaded_by_fkey"),
    sa.ForeignKeyConstraint(
        ["workspace_id"], ["workspace.id"], name="source_file_workspace_id_fkey"
    ),
    sa.CheckConstraint(
        "(status = 'removed'::file_status) = (removed_at IS NOT NULL)",
        name="source_file_removed_ck",
    ),
)

_tables["audit_log"] = sa.Table(
    "audit_log",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("seq", sa.BigInteger(), nullable=False),
    sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
    sa.Column(
        "actor_role",
        postgresql.ENUM("ap_executive", "manager", "auditor", name="user_role", create_type=False),
    ),
    sa.Column("action", sa.Text(), nullable=False),
    sa.Column("target_type", sa.Text(), nullable=False),
    sa.Column("target_id", postgresql.UUID(as_uuid=True)),
    sa.Column("before_state", postgresql.JSONB()),
    sa.Column("after_state", postgresql.JSONB()),
    sa.Column("reason", sa.Text()),
    sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column(
        "prev_hash", postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False)
    ),
    sa.Column(
        "row_hash",
        postgresql.DOMAIN("d_hash", sa.Text(collation="default"), create_type=False),
        nullable=False,
    ),
    sa.PrimaryKeyConstraint("id", name="audit_log_pkey"),
    sa.UniqueConstraint("seq", name="audit_log_seq_key"),
    sa.ForeignKeyConstraint(["actor_id"], ["app_user.id"], name="audit_log_actor_id_fkey"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], name="audit_log_workspace_id_fkey"),
)

sa.Index(
    "vms_pair_uq",
    _tables["vendor_merge_suggestion"].c["workspace_id"],
    _tables["vendor_merge_suggestion"].c["vendor_a_id"],
    _tables["vendor_merge_suggestion"].c["vendor_b_id"],
    unique=True,
)
sa.Index(
    "vms_pending_ix",
    _tables["vendor_merge_suggestion"].c["workspace_id"],
    unique=False,
    postgresql_where=sa.text("(state = 'pending'::suggestion_state)"),
)

sa.Index(
    "vendor_active_ix",
    _tables["vendor"].c["workspace_id"],
    unique=False,
    postgresql_where=sa.text("(merged_into IS NULL)"),
)
sa.Index(
    "vendor_name_trgm",
    _tables["vendor"].c["normalized_name"],
    unique=False,
    postgresql_using="gin",
    postgresql_ops={"normalized_name": "gin_trgm_ops"},
)
sa.Index(
    "vendor_pan_ix",
    _tables["vendor"].c["workspace_id"],
    _tables["vendor"].c["pan"],
    unique=False,
    postgresql_where=sa.text("(pan IS NOT NULL)"),
)


sa.Index(
    "vendor_alias_trgm",
    _tables["vendor_alias"].c["alias_norm"],
    unique=False,
    postgresql_using="gin",
    postgresql_ops={"alias_norm": "gin_trgm_ops"},
)
sa.Index("vendor_alias_vendor_ix", _tables["vendor_alias"].c["vendor_id"], unique=False)

sa.Index(
    "batch_recent_ix",
    _tables["batch"].c["workspace_id"],
    _tables["batch"].c["created_at"].desc(),
    unique=False,
)


sa.Index(
    "field_evidence_line_ix",
    _tables["field_evidence"].c["doc_line_id"],
    _tables["field_evidence"].c["field_name"],
    unique=False,
    postgresql_where=sa.text("(doc_line_id IS NOT NULL)"),
)
sa.Index(
    "field_evidence_version_ix",
    _tables["field_evidence"].c["document_version_id"],
    _tables["field_evidence"].c["field_name"],
    unique=False,
    postgresql_where=sa.text("(document_version_id IS NOT NULL)"),
)

sa.Index("link_current_from_ix", _tables["link_current"].c["from_document_id"], unique=False)
sa.Index("link_current_to_ix", _tables["link_current"].c["to_document_id"], unique=False)

sa.Index("link_from_ix", _tables["link"].c["from_document_id"], unique=False)
sa.Index(
    "link_to_state_ix",
    _tables["link"].c["to_document_id"],
    _tables["link"].c["state"],
    unique=False,
)


sa.Index(
    "opening_balance_po_ix",
    _tables["opening_balance"].c["workspace_id"],
    _tables["opening_balance"].c["canonical_po_no"],
    _tables["opening_balance"].c["po_line_no"],
    unique=False,
    postgresql_where=sa.text("(withdrawn_at IS NULL)"),
)

sa.Index("ai_call_log_doc_ix", _tables["ai_call_log"].c["document_id"], unique=False)
sa.Index(
    "ai_call_log_run_ix",
    _tables["ai_call_log"].c["run_id"],
    unique=False,
    postgresql_where=sa.text("(run_id IS NOT NULL)"),
)
sa.Index(
    "ai_call_log_time_ix",
    _tables["ai_call_log"].c["workspace_id"],
    _tables["ai_call_log"].c["created_at"].desc(),
    unique=False,
)

sa.Index(
    "export_file_expiry_ix",
    _tables["export_file"].c["expires_at"],
    unique=False,
    postgresql_where=sa.text("(status = 'ready'::export_status)"),
)
sa.Index(
    "export_file_run_ix",
    _tables["export_file"].c["run_id"],
    _tables["export_file"].c["filter_hash"],
    unique=False,
)


sa.Index(
    "dirty_scope_open_uq",
    _tables["dirty_scope"].c["workspace_id"],
    _tables["dirty_scope"].c["scope_kind"],
    _tables["dirty_scope"].c["scope_id"],
    unique=True,
    postgresql_where=sa.text("(processed_at IS NULL)"),
)
sa.Index(
    "dirty_scope_work_ix",
    _tables["dirty_scope"].c["workspace_id"],
    _tables["dirty_scope"].c["first_marked_at"],
    unique=False,
    postgresql_where=sa.text("(processed_at IS NULL)"),
)

sa.Index(
    "processing_event_doc_ix",
    _tables["processing_event"].c["document_id"],
    _tables["processing_event"].c["occurred_at"],
    unique=False,
)
sa.Index(
    "processing_event_err_ix",
    _tables["processing_event"].c["workspace_id"],
    _tables["processing_event"].c["occurred_at"].desc(),
    unique=False,
    postgresql_where=sa.text("(outcome = 'error'::stage_outcome)"),
)
sa.Index(
    "processing_event_file_ix",
    _tables["processing_event"].c["source_file_id"],
    _tables["processing_event"].c["occurred_at"],
    unique=False,
)

sa.Index("idempotency_key_expiry_ix", _tables["idempotency_key"].c["expires_at"], unique=False)

sa.Index(
    "vendor_gstin_pan_ix",
    _tables["vendor_gstin"].c["workspace_id"],
    _tables["vendor_gstin"].c["pan"],
    unique=False,
)
sa.Index("vendor_gstin_vendor_ix", _tables["vendor_gstin"].c["vendor_id"], unique=False)

sa.Index("document_batch_ix", _tables["document"].c["batch_id"], unique=False)
sa.Index(
    "document_canon_trgm",
    _tables["document"].c["canonical_doc_no"],
    unique=False,
    postgresql_using="gin",
    postgresql_ops={"canonical_doc_no": "gin_trgm_ops"},
)
sa.Index(
    "document_live_ix",
    _tables["document"].c["workspace_id"],
    _tables["document"].c["vendor_id"],
    _tables["document"].c["doc_type"],
    unique=False,
    postgresql_where=sa.text("(withdrawn_at IS NULL)"),
)
sa.Index(
    "document_natural_uq",
    _tables["document"].c["workspace_id"],
    _tables["document"].c["natural_key"],
    unique=True,
)
sa.Index(
    "document_vendor_ix",
    _tables["document"].c["workspace_id"],
    _tables["document"].c["doc_type"],
    _tables["document"].c["vendor_id"],
    _tables["document"].c["doc_date"],
    unique=False,
)

sa.Index("document_warning_doc_ix", _tables["document_warning"].c["document_id"], unique=False)
sa.Index("document_warning_file_ix", _tables["document_warning"].c["source_file_id"], unique=False)
sa.Index(
    "document_warning_open_ix",
    _tables["document_warning"].c["workspace_id"],
    _tables["document_warning"].c["code"],
    _tables["document_warning"].c["created_at"].desc(),
    unique=False,
    postgresql_where=sa.text("(resolved_at IS NULL)"),
)


sa.Index(
    "po_line_ledger_grni_ix",
    _tables["po_line_ledger"].c["workspace_id"],
    _tables["po_line_ledger"].c["po_document_id"],
    unique=False,
    postgresql_where=sa.text("(grni_qty > (0)::numeric)"),
)

sa.Index("ledger_entry_doc_ix", _tables["ledger_entry"].c["source_document_id"], unique=False)
sa.Index(
    "ledger_entry_line_uq",
    _tables["ledger_entry"].c["po_line_ledger_id"],
    _tables["ledger_entry"].c["source_doc_line_id"],
    unique=True,
    postgresql_where=sa.text("(source_doc_line_id IS NOT NULL)"),
)


sa.Index(
    "recon_run_active_ix",
    _tables["recon_run"].c["workspace_id"],
    unique=False,
    postgresql_where=sa.text("(status = ANY (ARRAY['queued'::run_status, 'running'::run_status]))"),
)
sa.Index(
    "recon_run_recent_ix",
    _tables["recon_run"].c["workspace_id"],
    _tables["recon_run"].c["queued_at"].desc(),
    unique=False,
)

sa.Index(
    "case_current_list_ix",
    _tables["case_current"].c["workspace_id"],
    _tables["case_current"].c["severity_rank"],
    _tables["case_current"].c["invoice_date"],
    _tables["case_current"].c["invoice_id"],
    unique=False,
)
sa.Index("case_current_run_ix", _tables["case_current"].c["run_id"], unique=False)
sa.Index(
    "case_current_status_ix",
    _tables["case_current"].c["workspace_id"],
    _tables["case_current"].c["effective_status"],
    _tables["case_current"].c["invoice_date"],
    unique=False,
)
sa.Index(
    "case_current_vendor_ix",
    _tables["case_current"].c["workspace_id"],
    _tables["case_current"].c["vendor_id"],
    _tables["case_current"].c["effective_status"],
    unique=False,
)

sa.Index("document_version_file_ix", _tables["document_version"].c["source_file_id"], unique=False)
sa.Index(
    "document_version_governing_ix",
    _tables["document_version"].c["document_id"],
    _tables["document_version"].c["revision_rank"].desc(),
    _tables["document_version"].c["seq"].desc(),
    unique=False,
)
sa.Index(
    "document_version_irn_ix",
    _tables["document_version"].c["workspace_id"],
    _tables["document_version"].c["irn"],
    unique=False,
    postgresql_where=sa.text("(irn IS NOT NULL)"),
)
sa.Index(
    "document_version_po_refs_ix",
    _tables["document_version"].c["canonical_po_refs"],
    unique=False,
    postgresql_using="gin",
)


sa.Index(
    "recon_case_invoice_ix",
    _tables["recon_case"].c["invoice_id"],
    _tables["recon_case"].c["created_at"].desc(),
    unique=False,
)
sa.Index(
    "recon_case_reason_gin",
    _tables["recon_case"].c["reason_codes"],
    unique=False,
    postgresql_using="gin",
)
sa.Index(
    "recon_case_run_status_ix",
    _tables["recon_case"].c["run_id"],
    _tables["recon_case"].c["status"],
    unique=False,
)
sa.Index(
    "recon_case_vendor_ix",
    _tables["recon_case"].c["workspace_id"],
    _tables["recon_case"].c["vendor_id"],
    _tables["recon_case"].c["status"],
    unique=False,
)

sa.Index(
    "recon_line_result_po_ix",
    _tables["recon_line_result"].c["po_document_id"],
    _tables["recon_line_result"].c["po_line_no"],
    unique=False,
)
sa.Index(
    "recon_line_result_reason_gin",
    _tables["recon_line_result"].c["reason_codes"],
    unique=False,
    postgresql_using="gin",
)
sa.Index("recon_line_result_run_ix", _tables["recon_line_result"].c["run_id"], unique=False)


sa.Index(
    "case_action_actor_ix",
    _tables["case_action"].c["actor_id"],
    _tables["case_action"].c["occurred_at"].desc(),
    unique=False,
)
sa.Index(
    "case_action_bulk_ix",
    _tables["case_action"].c["bulk_id"],
    unique=False,
    postgresql_where=sa.text("(bulk_id IS NOT NULL)"),
)
sa.Index(
    "case_action_invoice_ix",
    _tables["case_action"].c["invoice_id"],
    _tables["case_action"].c["occurred_at"].desc(),
    unique=False,
)

sa.Index(
    "secret_expiry_ix",
    _tables["secret"].c["expires_at"],
    unique=False,
    postgresql_where=sa.text("(expires_at IS NOT NULL)"),
)

sa.Index("session_token_uq", _tables["session"].c["token_hash"], unique=True)
sa.Index(
    "session_user_ix",
    _tables["session"].c["user_id"],
    unique=False,
    postgresql_where=sa.text("(revoked_at IS NULL)"),
)

sa.Index(
    "rate_limit_email_ix",
    _tables["rate_limit_event"].c["workspace_id"],
    _tables["rate_limit_event"].c["kind"],
    _tables["rate_limit_event"].c["email"],
    _tables["rate_limit_event"].c["occurred_at"].desc(),
    unique=False,
)
sa.Index(
    "rate_limit_ip_ix",
    _tables["rate_limit_event"].c["workspace_id"],
    _tables["rate_limit_event"].c["kind"],
    _tables["rate_limit_event"].c["ip"],
    _tables["rate_limit_event"].c["occurred_at"].desc(),
    unique=False,
)

sa.Index(
    "app_user_email_uq",
    _tables["app_user"].c["workspace_id"],
    _tables["app_user"].c["email"],
    unique=True,
)


sa.Index(
    "source_file_batch_ix",
    _tables["source_file"].c["workspace_id"],
    _tables["source_file"].c["batch_id"],
    _tables["source_file"].c["status"],
    unique=False,
)
sa.Index(
    "source_file_parent_ix",
    _tables["source_file"].c["parent_file_id"],
    unique=False,
    postgresql_where=sa.text("(parent_file_id IS NOT NULL)"),
)
sa.Index(
    "source_file_sha_uq",
    _tables["source_file"].c["workspace_id"],
    _tables["source_file"].c["sha256"],
    unique=True,
)
sa.Index(
    "source_file_status_ix",
    _tables["source_file"].c["workspace_id"],
    _tables["source_file"].c["status"],
    _tables["source_file"].c["uploaded_at"].desc(),
    unique=False,
)

sa.Index(
    "audit_log_actor_ix",
    _tables["audit_log"].c["workspace_id"],
    _tables["audit_log"].c["actor_id"],
    _tables["audit_log"].c["seq"].desc(),
    unique=False,
)
sa.Index(
    "audit_log_chain_ix",
    _tables["audit_log"].c["workspace_id"],
    _tables["audit_log"].c["seq"].desc(),
    unique=False,
)
sa.Index(
    "audit_log_target_ix",
    _tables["audit_log"].c["workspace_id"],
    _tables["audit_log"].c["target_type"],
    _tables["audit_log"].c["target_id"],
    _tables["audit_log"].c["seq"].desc(),
    unique=False,
)

__all__ = ["metadata"]
