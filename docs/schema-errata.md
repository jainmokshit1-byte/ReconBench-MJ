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

## Revision 0021: dark consensus provenance (accuracy plan AC-6b, 8 Oct 2026)

`0021_consensus` adds `consensus_run` for queued and terminal job status/summary, and `consensus_decision` for one immutable trust, reader, crop and validator record per field. A successful vision answer schedules the consensus follow-up in the same transaction; the job uses the stored `ai_cache` response by `cache_key`, rereads only masked page-image crops in the isolated OCR child, and purges page bytes after completion or failure. AC-6b is dark: it does not change a document/version or apply `auto_verified`/`reopened` decisions.

`consensus_decision` has a row mutation guard and denies `UPDATE`, `DELETE` and `TRUNCATE` to `reconbench_app`; the report role has no access to either new table. The schema metadata mirrors all columns, defaults, constraints and indexes; CI's migration-safety baseline now upgrades to `0020`, and the migration-acceptance test recognizes head `0021`.

Validation on fresh disposable PostgreSQL 16.15 clusters: an empty database upgrades to `0021`; `check_migration_safety.py` clones `0020` and upgrades the copy to `0021`; Alembic drift, catalog, index, decision, seed, queue-schema and table-count checks pass. The contract contains 48 application tables, 38 enums, 25 triggers, 161 foreign keys, 256 explicit ACL entries and 7 default privileges; after applying the queue schema the count is 48 application tables + 4 Procrastinate tables. `backend/tests/integration/test_ai_vision.py` covers a dark consensus run, persisted field provenance, unchanged document state and page-byte purge.

## Revision 0022: per-field automatic verification consent (accuracy plan AC-6c, 8 Oct 2026)

`0022_auto_verify` adds `ai_setting.auto_verify_field_types TEXT[] NOT NULL DEFAULT '{}'`, constrained to the five D42 field types whose offline benchmark gates pass, and nullable `consensus_run.result_version_id` referencing the immutable `document_version` produced when a consensus outcome is applied. The empty default keeps every existing workspace opted out; the legacy consent fingerprint remains byte-identical with this setting off. A selected field set is included in consent and rechecked when the consensus result commits, so a setting change cannot apply a stale decision.

The migration reflects AC-6c's required persisted opt-in and applied-version link; it is a documented deviation from the original no-migration estimate. The metadata, OpenAPI query contract and schema contract were updated, and CI's previous-schema migration baseline and acceptance test now use `0021` and `0022` respectively.

Validation on disposable PostgreSQL 16.15 clusters: a fresh database upgrades to `0022`; `check_migration_safety.py` clones `0021` and upgrades the copy to `0022`; Alembic drift, catalog, index, decision, seed, queue-schema and table-count checks pass. The contract reports 48 application tables, 38 enums, 25 triggers, 162 foreign keys, 256 explicit ACL entries and 7 default privileges; with queue schema, 48 application tables + 4 Procrastinate tables.

## Revision 0023: exact document identity evidence (accuracy plan AC-8b, 8 Oct 2026)

`0023_document_identity` adds an append-only `document_identity` row per document version. It stores the source file SHA-256, source-page locator, ordered 64-bit page dHashes and nullable content fingerprint; it stores no page images or extracted text. Existing versions are backfilled with source SHA and locator only, leaving page hashes empty and content fingerprint null so the app can use the exact-source fallback until new parser evidence exists. A workspace/content-fingerprint index and a workspace/source-locator index support the exact match paths. Row and truncate mutation triggers protect the evidence; `reconbench_app` cannot update/delete/truncate it and `reconbench_report` has no access.

The migration supports AC-8b's exact page-hash plus content-fingerprint rescan match, with source SHA plus locator when fingerprints cannot be built. It does not alter page images, document-version history or application/API contracts. CI's previous-schema baseline and the Phase 4.11/4.13 fixed revision set now use `0022` and `0023` respectively.

Validation on disposable PostgreSQL 16.15 at `127.0.0.1:15499`: a fresh database upgrades to `0023`; `check_migration_safety.py` clones `0022` and upgrades the copy to `0023`; Alembic drift, catalog, index and decision checks pass. A separate synthetic pre-`0023` document version upgrades with its exact source SHA and locator backfilled, empty page hashes and null content fingerprint. The schema contract reports 49 application tables, 38 enums, 27 triggers, 164 foreign keys, 258 explicit ACL entries and 7 default privileges; with queue schema, 49 application tables + 4 Procrastinate tables. `test_phase4_task_411_413.py` passes at head `0023`.

## Revision 0024: matching field provenance (accuracy plan AC-9a, 8 Oct 2026)

`0024_matching_provenance` makes `field_evidence.confidence` nullable and adds `origin`, `verification`, `verified_by` and `verified_at`. Existing manual evidence is backfilled as user-verified with confidence cleared; parser evidence is classified as OCR or ERP import from its extraction method where known. Origin and verification CHECK constraints and partial indexes support latest-reference trust checks. Unknown legacy extraction origins remain null rather than being inferred.

This persistence change supports the AC-9a rule that extraction source and confidence do not themselves mean verified. Manual values remain verified independent of confidence, while OCR/ERP references can be system-checked only against an exact same-vendor open PO or an existing same-vendor GRN→PO relation with independent evidence. No link-confirm API or UI was added.

Validation on disposable PostgreSQL 16.15 at `127.0.0.1:15497`/`15499`: migration safety cloned `0023` and upgraded the copy to `0024`; schema drift, catalog, index, decision, seed, queue schema and table-count checks passed. The contract remains at 49 application tables, 38 enums, 27 triggers, 164 foreign keys, 258 explicit ACL entries and 7 default privileges; with queue schema, 49 application tables + 4 Procrastinate tables. The matching benchmark and session check outcomes are recorded in D57.

## Revision 0025: vision reader parameters and cache provenance (LLM extraction plan WI-BE5, 16 Oct 2026)

`0025_ai_reader_params` adds `ai_request.reader_params JSONB NOT NULL DEFAULT '{}'` (CHECK: a JSON object) and three nullable columns on `ai_cache`: `provider ai_provider`, `schema_version TEXT` (1–64 characters) and `reader_params JSONB` (a JSON object). No data is rewritten: existing requests read `{}`, existing cache entries keep `NULL` provenance.

Why: every AI result must record provider, model, prompt, schema and reader/image parameters, and the vision cache key must include them (`cache_key_v2`, D63), so an answer read with other settings (another provider, thinking, sampling, image size or pass) is never reused. Text hooks H1–H4 keep the v1 key and `{}`; old vision entries are no longer hit and expire under the normal TTL.

Validation on disposable PostgreSQL 16.15 at `127.0.0.1:15497`: migration safety cloned `0024` and upgraded the copy to `0025`; Alembic drift (no metadata drift), catalog, index, decision, seed, queue schema and table-count checks passed. The contract (which tracks no columns or check constraints) is unchanged: 49 application tables, 38 enums, 27 triggers, 164 foreign keys, 258 explicit ACL entries and 7 default privileges; with queue schema, 49 application tables + 4 Procrastinate tables. CI's previous-schema baseline is now `0024`; the Phase 4.11/4.13 fixed revision set includes `0025` and compares with it.

## Revision 0028: provider connections, defaults and cost-cap fields (accuracy plan WI-PV1, 20 Oct 2026)

`0028_provider_connections` is the data model for connecting and choosing LLM providers (plan section 4.15, decision-log D80/D81). No application code reads the new columns yet; WI-PV2 .. PV7 add the behaviour.

- `ai_provider_config` gains connection state (`connected_at`, `last_error_code`, `last_error_at`, `key_rotated_at`), the consent accepted at connect (`consent_fingerprint`, `consent_accepted_by`, `consent_accepted_at`; all three or none), the Gemini cost-cap fields in rupees (`monthly_cost_cap_inr` and `doc_cost_flag_inr` as `d_amount`, `cap_warn_pct` 1 to 100 default 80), a per-provider `auto_verify_field_types` list (same five values as `ai_setting`, empty by default), `apply_mode` (default `suggest`) and the circuit-breaker state (`closed`/`open`, `breaker_changed_at`). The check `ai_provider_config_key_ck` (only a `vllm` card may reference a key) is dropped so the Gemini key can move to its card; the move itself is WI-PV2.
- `ai_setting` gains `extraction_provider`, `assist_provider`, `shadow_provider` (nullable `ai_provider`) and `allow_provider_fallback` (default false). `reader_strategy` and `primary_reader` stay readable for one release.
- `batch.provider_override` (nullable `ai_provider`): the provider chosen for one upload.
- New append-only `ai_endpoint_allow` (workspace allowlist log: `add`/`remove`, URL up to 512 characters) and `provider_measurement` (counts-only measurement per provider, field type and split, with a lower bound `d_score`). Both have the `forbid_mutation` trigger, `UPDATE/DELETE/TRUNCATE` revoked from `reconbench_app`, and no access for `reconbench_report`.
- Backfill: Gemini workspaces keep Gemini as extraction and assist provider (a workspace whose reader was single on another provider keeps it for extraction); a reader that ran in `shadow` becomes `shadow_provider`; every Gemini card gets the owner's values 2,500 / 80 / 20 (decision D81); every per-provider auto-verify list starts empty. The workspace-wide `ai_setting.auto_verify_field_types` is not changed by the migration.

Validation on a fresh disposable `postgres:16.15-bookworm` at `127.0.0.1:15497`: `check_migration_safety.py` cloned `0027` and upgraded the copy to `0028`; Alembic autogenerate reports no metadata drift; `schema_contract.json` was regenerated from the clean head database before the seed and queue schema (53 tables, 40 enums, 30 triggers, 175 foreign keys, 268 explicit ACLs, 7 default privileges); the drift, catalog, index and decision checks pass; after the seed and queue schema the table-count check passes (53 plus 4 Procrastinate tables). The new `test_ai_provider_connections_migration.py` (10 tests) rewinds a clone to `0027`, seeds legacy workspaces and checks the backfill, constraints, grants and the mutation guard. Nothing was applied to the live database.

## Revision 0029: Gemini key on its card, key tier, rotation candidate (accuracy plan WI-PV2, 20 Oct 2026)

`0029_provider_key_move` finishes the credential model of WI-PV1. The Gemini key reference moves from `ai_setting.key_secret_id` to the Gemini row of `ai_provider_config`; the application keeps reading the old location when the card has no key (one release, until WI-BE9a).

- `ai_provider_config.key_tier` (`free` or `paid`, default `free`, NOT NULL): declared by the admin at connect because the provider API cannot say which tier a key belongs to (decision-log D83/D84). A vLLM card must be `free` (CHECK). Every existing card, including the live Gemini card, migrates as `free` (owner, D84).
- `ai_provider_config.pending_key_secret_id` (nullable FK to `secret`): the candidate key of a rotation; the old key stays active until the candidate passes a provider test, then the worker swaps them. CHECK: it is never the live key.
- The move changes only the pointer: the `secret` row (ciphertext, nonce, key id, hint) is neither copied nor re-encrypted. The old pointer is cleared only after the migration verified, per moved key, that the card points at a secret of the same workspace that still holds ciphertext and nonce; a mismatch, or a card that already holds a different key, raises and rolls the migration back. A Gemini card whose legacy test passed takes that test time as `connected_at`. A workspace with a Gemini key but no card gets one (owner cap values 2,500 / 80 / 20).
- `downgrade()` is real for this revision: it moves the pointer back with the same verify-then-clear order, refuses when both locations hold a key, deletes pending rotation candidates and drops the two columns.

Validation on a fresh disposable `postgres:16.15-bookworm` at `127.0.0.1:15497`: `check_migration_safety.py` cloned `0028` and upgraded the copy to `0029`; Alembic autogenerate reports no metadata drift; schema drift, catalog (53 tables, 176 foreign keys), index and decision checks pass; `test_ai_provider_key_move_migration.py` (dummy sealed key, round-trip decrypt, rollback on conflict, downgrade, no key value in output) passes. Nothing was applied to the live database.

## Revision 0030: last-manager lock for `ai.manage` (accuracy plan WI-PV3, decision O-18, decision-log D90, 21 Oct 2026)

`0030_last_manager_lock` adds the database half of WI-PV3. The application now checks `ai.manage` against the stored role (`app_user.role = 'manager'` and `is_active`) on every AI-settings and provider write; every other capability keeps the P0 policy (all persisted users act as Manager; the main plan is not edited, the departure is O-18).

- `app_user_last_manager_guard()` and the deferred constraint trigger `app_user_last_manager` (AFTER UPDATE OF `role`, `is_active`, `workspace_id` OR DELETE ON `app_user`, DEFERRABLE INITIALLY DEFERRED, FOR EACH ROW): at COMMIT, a transaction that demoted, deactivated, moved or deleted an active manager fails with `check_violation` (`last_manager`) when the workspace is left without an active manager. A multi-row demotion of every manager is refused; swapping managers inside one transaction is allowed; removing a whole workspace (its users, then the workspace row, in one transaction) is allowed; a workspace that never had a manager is not blocked. A per-workspace advisory lock taken at the check serialises concurrent demotions.
- No table, column, enum, foreign key or ACL changes; the contract gains one routine and one trigger (38 routines, 31 triggers).
- `downgrade()` drops the trigger and the function.

Validation on a fresh disposable `postgres:16.15-bookworm` at `127.0.0.1:15497`: `check_migration_safety.py` cloned `0029` and upgraded the copy to `0030`; Alembic autogenerate reports no metadata drift; `schema_contract.json` was regenerated from the clean head database before the seed and queue schema (53 tables, 40 enums, 31 triggers, 38 routines, 176 foreign keys, 268 explicit ACLs, 7 default privileges); the drift, catalog, index and decision checks pass; after the seed and queue schema the table-count check passes (53 plus 4 Procrastinate tables). `test_ai_admin_enforcement.py` covers the lock (demote, deactivate, delete, multi-row, swap, concurrent demotions, whole-workspace removal, a workspace without a manager). Nothing was applied to the live database.
