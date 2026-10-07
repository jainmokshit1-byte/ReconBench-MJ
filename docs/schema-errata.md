# Schema DDL validation and errata

**Date:** 2026-10-01
**Task:** Phase 2, Task 2.1 (G-22)
**Source:** `docs/ReconBench_Backend_Schema.md`, §§3–8
**Database:** Fresh disposable `postgres:16.15-bookworm`; PostgreSQL 16.15; database `reconbench_schema_scratch`

## Validation result

The schema-definition and privilege SQL blocks from §§3–8 were executed in source order against a blank database using `psql -X -v ON_ERROR_STOP=1`. The final run exited successfully. The catalog contains 41 public base tables, 4 views, and 37 public functions; the `pg_trgm` extension and all 4 declared `reconbench_*` roles were created. The scratch instance was isolated from the Phase 1 Compose database and discarded after validation. No real sample files were opened or used.

## Errata

| Location | Finding | Fix / disposition |
|---|---|---|
| Schema §§3–8 | No PostgreSQL DDL or privilege-block errors were found in the final ordered run. | No source DDL correction was needed. |
| Schema §4.8, line 1261; §4.11, line 1524 | These fenced SQL blocks begin with parameterized runtime `INSERT` statements (`$1`, `$2`, etc.), not schema DDL. Passing them to `psql` as standalone DDL produced “there is no parameter $1”. | Excluded these two application DML examples from the DDL run. They are not schema defects and remain unchanged. |

No migrations, seed scripts, or application behavior were started as part of Task 2.1. The user-confirmed product defaults in the Implementation Plan remain unchanged.

## Revision 0017: AI Assist additions (4 Oct 2026)

Revision `0017_ai_assist_ledger` deliberately extends the validated Backend Schema for the AI Assist work recorded in `docs/decision-log.md` (D4, D6, D8, D10). The source schema documents are unchanged; these are reviewed additions, not corrections to them.

| Object | Addition | Reason / deviation from the AI Assist plan |
|---|---|---|
| `ai_request` (new) | Durable request ledger: subject, settings snapshot, `request_key`/`cache_key`/`attempt_no`, `request_state`, `apply_outcome`, token and USD reservations, actual usage (including thought tokens), timing and acknowledgement. No document text. | D8. `call_kind` (`hook`, `provider_test`, `synthetic_smoke`) was added because provider tests and synthetic smoke calls are budgeted but are not `ai_hook` values. Uniqueness is `(workspace_id, request_key, attempt_no)` plus a partial unique index allowing one live attempt per `request_key`; an unacknowledged `uncertain` attempt stays live. The plan's `ai_request.ai_call_log_id` was **not** added: `ai_call_log.ai_request_id` already links the two, and a reverse key would create a second foreign-key cycle. `operation_key` carries the per-run/per-operation budget scope. |
| `ai_worker_heartbeat` (new) | Global singleton `(worker_role = 'ai', instance, started_at, last_seen_at)`. | Status label "AI worker unavailable". Global like `worker_control`, so it has no `workspace_id`. |
| `ai_line_opinion` (new) | Append-only H2 opinion per `recon_line_result` (row and TRUNCATE triggers; the app role has SELECT and INSERT only; the report role has no access), reason ≤ 300 characters. | D10. `recon_line_result` itself remains append-only and is never updated. One opinion per line result. |
| `secret` | `session_id` with composite FK `(workspace_id, session_id, created_by) → session(workspace_id, id, user_id)`; `created_by` is now `NOT NULL`; `(session_id IS NULL) = (expires_at IS NULL)`. The revision refuses to run if `secret` has rows. | D6. A session-only key is bound in the database to the saving user's session; saved keys have neither a session nor an expiry. The table was unused before this revision, and keys sealed under the earlier symmetric design must not be silently carried over. |
| `session` | `UNIQUE (workspace_id, id, user_id)`. | Target of the composite FK above; workspace-first, so no new uniqueness exception is needed. |
| `link` | `link_ai_call_uq`: unique `ai_call_id` where not null. | H3 applies at most one link per AI call. Reviewed non-workspace-first exception (category "AI call"): `ai_call_log` ids are globally unique. |
| `ai_call_log` | `ai_request_id` (FK) and `tokens_thought` (≥ 0). | Phase 1 confirmed the Interactions API reports thought tokens separately. |
| `ai_setting` | `consent_fingerprint` and `ai_setting_consent_ck`: consent requires a fingerprint, consenting user, time and consent-text version. Any row that already had consent is reset to `rules_only` without consent (and its `version` bumped) before the CHECK is added. | Re-consent whenever the fingerprint changes needs the fingerprint that was actually consented to; recomputing it from current settings could never detect a change. This column was not listed in the plan's §5.1 and is added here. |
| Grants | `REVOKE SELECT ON ai_cache FROM reconbench_report`. | Cached provider output is not reporting data; the retention role keeps SELECT and DELETE. |

`ai_request.reserved_usd` and `actual_usd` use `NUMERIC(12,6)`, matching `ai_call_log.cost_usd`; the catalog check lists them as reviewed schema-specific precisions. The `ai_outcome` enum is unchanged.

Validation on a fresh disposable `postgres:16.15-bookworm` container: an empty database upgrades to `0017`; a copy at `0016` upgrades to `0017` (`check_migration_safety.py`); a second `upgrade head` is a no-op; the drift, catalog, index and decision checks pass; seeding is idempotent; and after the separate queue step the table count is 45 ReconBench + 4 Procrastinate tables. `backend/tests/integration/test_ai_assist_migration.py` covers constraints, live-attempt uniqueness, the composite FK, append-only behaviour, grants as the actual group roles, and the empty-`secret` guard.

## Revision 0018: undated invoices in `kpi_summary` (5 Oct 2026)

`0018_kpi_summary_undated` replaces `kpi_summary(uuid, date, date)` with `CREATE OR REPLACE`; signature, return columns, owner (`reconbench_migrator`) and grants are unchanged. The period predicate becomes `invoice_date IS NULL OR invoice_date BETWEEN p_from AND p_to`, the same rule the export filter already applies (decision-log D19 #5), so Home KPIs equal the Excel Summary for the same period. This is a reviewed deviation from Backend Schema §7 (`invoice_date BETWEEN p_from AND p_to`); the source document is unchanged. Only the routine hash in `schema_contract.json` changed.

Validation on a fresh disposable `postgres:16.15-bookworm`: `check_migration_safety.py` cloned `0017` and upgraded the copy to `0018`; a second `upgrade head` is a no-op; the drift, catalog, index and decision checks pass. `backend/tests/integration/test_kpi_summary.py` checks that an undated invoice is counted in every period while a dated invoice outside the period is not.

## Revision 0020: vision reader (accuracy plan AC-5, 7 Oct 2026)

`0020_vision_pages` adds what the page-image reader needs (decision-log D37):

- `ai_hook` gains `h6_vision`. The core `AIHook` enum still mirrors the `0001` contract that `test_core_enums_match_all_database_enum_values` checks (the same as `vendor_name_status` from `0019`); the application's hook list is `HookName` in `ai/contracts.py`.
- `ai_setting.vision_enabled boolean NOT NULL DEFAULT false`: the workspace opted into page images in the AI consent flow. Existing rows stay off, so their consent fingerprints are unchanged.
- `document_page_image` (PK `document_version_id, page`; `workspace_id NOT NULL`): one row per masked page of a parsed version, with `image_sha256`, MIME type, size, `render` (geometry: source, rotation, skew, working size, PDF size) and `mask` (mask version, counts, boxes on the 0–1000 page scale). The `image` bytes are transient: the AI worker clears them once no queued, reserved or sending vision request uses the version (CHECK `(image IS NULL) = (purged_at IS NOT NULL)`, `octet_length(image) = byte_size`). Partial index `document_page_image_live_ix (workspace_id, document_version_id) WHERE image IS NOT NULL`. `reconbench_report` has no access.

Validation on fresh disposable `postgres:16.15-bookworm` containers: an empty database upgrades to `0020`; `check_migration_safety.py` cloned `0019` and upgraded the copy to `0020`; the drift (Alembic: no metadata drift), catalog, index and decision checks pass. The contract now counts 46 application tables, 154 foreign keys and 250 explicit ACL entries (enums stay 38). `backend/tests/integration/test_ai_vision.py` covers the page rows, the purge and the request flow.
