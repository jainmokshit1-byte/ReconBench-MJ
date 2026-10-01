# Phase 2 Task 2.9: index review

Reviewed the query paths in Implementation Plan §2.9 against Backend Schema §6.4 and TRD §6.4. The checks are implemented in `scripts/check_phase2_indexes.py`; they compare index name, table, ordered key columns, uniqueness, access method, and the required partial-index predicate or trigram operator class against PostgreSQL's catalog.

| Planned access path | Catalog index | Verification |
|---|---|---|
| Document list by workspace, type, vendor, and date | `document_vendor_ix` | B-tree `(workspace_id, doc_type, vendor_id, doc_date)` |
| Document natural key unique within workspace | `document_natural_uq` | Unique B-tree `(workspace_id, natural_key)` |
| Incoming link picker by target and state | `link_to_state_ix` | B-tree `(to_document_id, state)` |
| Outgoing link lookup | `link_from_ix` | B-tree `(from_document_id)` |
| Reconciliation cases by run and status | `recon_case_run_status_ix` | B-tree `(run_id, status)` |
| Reconciliation reason-code filtering | `recon_case_reason_gin` | GIN `(reason_codes)` |
| Current case by invoice | `case_current_workspace_id_invoice_id_key` | Unique B-tree `(workspace_id, invoice_id)`; covers the planned invoice lookup and enforces one current case per workspace/invoice |
| Document lines by version and line number | `doc_line_document_version_id_line_no_key` | Unique B-tree `(document_version_id, line_no)` |
| Vendor normalized-name picker | `vendor_name_trgm` | GIN trigram on `normalized_name` |
| Canonical document-number picker | `document_canon_trgm` | GIN trigram on `canonical_doc_no` |
| Open dirty-scope de-duplication | `dirty_scope_open_uq` | Unique partial B-tree `(workspace_id, scope_kind, scope_id)` where `processed_at IS NULL` |
| Worker lookup of unprocessed scopes | `dirty_scope_work_ix` | Partial B-tree `(workspace_id, first_marked_at)` where `processed_at IS NULL` |

The check ran against the disposable PostgreSQL 16.15 Phase 2 database and verified all 12 planned index entries. `case_current` uses its workspace-scoped unique index for invoice lookup; no duplicate non-unique index was added. The queue library schema is applied separately and is not part of this index contract.
