# Phase 2 Task 2.3: metadata and drift checks

`backend/reconbench_app/db/metadata.py` is a checked-in SQLAlchemy 2.0 declaration for the validated Backend Schema sections 3–8. It declares 41 application tables, 657 columns, and their PostgreSQL types, defaults, computed columns, keys, checks, and indexes. It is static metadata; the application does not build it by reflecting the database it is checking.

Run `make schema-drift` with `RB_MIGRATOR_DATABASE_URL` configured. The checker first requires the database's Alembic revision to equal the migration head, runs Alembic's autogenerate `check`, and then compares PostgreSQL catalog objects against `backend/reconbench_app/db/schema_contract.json`. The drift target is the ReconBench schema after `make migrate` and before the separately managed Procrastinate schema step.

## What Alembic checks

Alembic compares the declared application tables, columns and types, server defaults, primary and foreign keys, unique and check constraints, and indexes. `alembic_version` is Alembic's own bookkeeping table and is excluded from application metadata. Views are not included by Alembic's table inspector.

The metadata check currently emits SQLAlchemy's warning that it cannot sort the `document` / `document_version` foreign-key cycle and therefore skips some foreign keys in that cycle. To avoid leaving that gap implicit, the catalog check compares the complete foreign-key inventory (138 constraints in the validated schema), not only those two tables.

## Explicit catalog coverage

Alembic autogenerate does not reliably validate the definitions or privileges of these PostgreSQL objects. The JSON contract and `scripts/check_schema_drift.py` compare them explicitly:

- `pg_trgm` extension name, version, and schema;
- every public enum and its ordered labels, plus every domain's base type, nullability, default, collation, and check constraints;
- view definitions, all public function/procedure definitions and relevant execution attributes, and trigger definitions/enabled states;
- application roles and memberships, explicit schema/table/column/sequence/routine ACL entries, default privileges, object owners, and sequence definitions/ownership;
- all application foreign-key definitions, including cycle constraints skipped by Alembic.

The Phase 2 baseline contains 2 extensions, 4 views, no materialized views, 37 routines, 22 triggers, 37 enums, 5 domains, 4 ReconBench roles, 1 sequence, 230 explicit ACL entries, and explicit default grants for future tables and sequences. The manifest also locks the public relation inventory and its owners. Function, view, and trigger definitions are represented by SHA-256 fingerprints; a catalog change therefore fails the check instead of being silently omitted.

## Workspace ownership and uniqueness review

Run `scripts/check_phase2_catalog.py` on PostgreSQL 16 before applying the separate queue schema. It treats `workspace` as the global root from Schema §4.1 and requires `workspace_id NOT NULL` on each of the other 40 ReconBench tables. It checks for float/double columns, the exact amount/rate/quantity/score/tax-rate domains, native numeric precisions, and text document/PO/GSTIN/HSN fields.

The workspace-first rule applies to **tenant-scoped secondary unique indexes**. Primary-key indexes identify rows and are not tenant-scoped natural-key uniqueness. The check requires `workspace_id` first on every other unique index except these reviewed schema invariants:

| Category | Reviewed indexes | Reason |
|---|---|---|
| Audit sequence | `audit_log_seq_key` | One global monotonic audit sequence |
| Parent/line or parent sequence | `doc_line_document_version_id_line_no_key`, `document_version_document_id_seq_key`, `ledger_entry_line_uq`, `ledger_entry_po_line_ledger_id_seq_key`, `opening_balance_source_file_id_row_no_key`, `recon_line_result_case_id_line_no_key` | Uniqueness is scoped to the referenced version, ledger line, source file, or case; those parent identifiers are globally unique |
| Run | `recon_case_run_id_invoice_id_key`, `run_manifest_run_id_key` | One case per invoice within a run and one manifest per run |
| Session token | `session_token_uq` | Token hashes are globally unique |

The checker asserts the exact exception names, tables, and ordered columns and fails on any new unreviewed non-workspace-first unique index. These are checks and documentation of the validated DDL; they do not rewrite it.

The Implementation Plan §2.4 amount/rate/quantity/score/tax precisions are checked alongside four additional native `NUMERIC` precisions explicitly present in the validated Backend Schema: `recon_case.variance_pct NUMERIC(9,4)`, `recon_line_result.rate_var_pct NUMERIC(9,4)`, `ai_setting.budget_cap_usd NUMERIC(12,4)`, and `ai_call_log.cost_usd NUMERIC(12,6)`. The catalog check asserts these exact table/column precisions so they remain visible and do not broaden the common money/rate/quantity rules.

The queue library remains outside this metadata and this Task 2.3 baseline. Apply its version-pinned schema only with `make queue-schema`, as a separate operation. The drift and catalog checks deliberately run against the app schema before that step; they do not claim to validate Procrastinate's schema. After the queue step, `scripts/check_phase2_table_count.py` verifies the 41 ReconBench tables and the locked Procrastinate 3.10.0 table set separately.

## Updating the contract

Do not refresh `schema_contract.json` merely to make a drift check pass. A schema change must first be reviewed against the validated Backend Schema and implemented through the existing forward-only Alembic chain; then update the static metadata and the catalog contract together, explaining any intentional difference in the relevant schema documentation.
