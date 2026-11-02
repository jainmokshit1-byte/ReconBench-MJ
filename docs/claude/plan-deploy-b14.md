# Deploy plan b14 — KI-012 vendor re-match + KI-013 removed-file re-upload (2 Nov 2026)

Status: **executed 2 Nov 2026 (D121).** Gate passed; deployed as live-20261102-b14. For the implementing agent (Antigravity); Claude reviews the report.
Rules: `CLAUDE.md`; never read `work/`, `.env`, `deploy/secrets/`; no AI provider calls; no real invoice data in commits, docs or report; no push.

## What ships (backend only)
| Part | Files | Effect on live |
|---|---|---|
| KI-012 | `backend/reconbench_app/jobs.py` (`enqueue_vendor_rematch`, `run_vendor_rematch`, task `reconbench.rematch_vendors`), `backend/reconbench_app/mapping_service.py` (`_enqueue_vendor_rematch` in assign / create / rename / merge), tests `tests/unit/test_jobs.py`, `tests/integration/test_vendor_rematch.py` | After a person's vendor change: one waiting job per workspace re-runs vendor matching, link matching and the ledger drain. Manual assignments kept; ambiguous names stay unassigned; links never confirmed by the system. |
| KI-013 | `backend/reconbench_app/ingestion.py` (multi-candidate reuse, collision-key owner check, `IngestionFailure` → failed file), `backend/reconbench_app/jobs.py` (`InfrastructureRetryStrategy`, `IntegrityError` → `INTEGRITY_ERROR` on attempt 1), test `tests/integration/test_reupload_withdrawn.py` | Re-uploading a removed PDF revives the most recently removed copy instead of crashing; unique-key errors fail once with a reason instead of 5 retries. |
| Records | `docs/claude/KNOWN_ISSUES.md`, `docs/decision-log.md`, `docs/claude/PROJECT_STATUS.md`, `docs/claude/handoff-grn-reupload-fix.md`, this file | — |

No web change, no migration (Alembic stays 0036), no rule-path change (ENGINE_VERSION stays 0.1.14, RULES_HASH unchanged).
Not in this deploy: `CODEBASE_CONTEXT.md`, `docs/claude/plan-accuracy-testing.md`, `docs/demo/` (leave untracked).

## Gate (offline / throwaway only; stop on any failure)
1. `scripts/check_rules_hash.py` (unchanged 63095a3a…733b), `scripts/check_engine_version.py --base-ref HEAD` (no rule path changed), `scripts/check_core_purity.py`, `lint-imports` (3 kept), `mypy --strict` core clean, `mypy reconbench_app` = 80.
2. Ruff check + format check on the 6 touched Python files.
3. Offline suite: expect ≥ 1549 passed, 22 skipped, 0 failed.
4. Integration on a throwaway PostgreSQL (recipe in `docs/claude/handoff-grn-reupload-fix.md`), one file per call: `test_reupload_withdrawn.py` (6), `test_vendor_rematch.py` (2), `test_ingestion.py` (4), `test_ac4_vendor_hygiene.py` (6), `test_pd_ledger_scopes.py` (3), `test_phase4_task_411_413.py` (3), `test_page_split.py` (2 known failures, KI-003 — report if different). Remove the container after.
5. `scripts/golden_run.py --self-check`: 0 false Matched.

## Deploy steps (live)
1. **Pre-checks (read-only, `PGOPTIONS=-c default_transaction_read_only=on`):** queue `todo`/`doing` = 0 (stop if not); Alembic 0036; record counts: `ai_request`, `link`, active/withdrawn `document`, `document_version`, unassigned active PO/GRN, `source_file` with status `failed`; switch position (`ai_setting` latest).
2. **Commits** on `phase2-hosted-ci`, two commits, attribution line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` is not required for Antigravity commits — use the owner's normal author; messages must not contain real invoice/PO numbers, names or amounts:
   - "KI-012: a person's vendor change re-runs vendor and link matching" (jobs.py rematch parts, mapping_service.py, test_jobs.py, test_vendor_rematch.py).
   - "KI-013: re-uploading a removed file revives its latest copy; unique-key errors are not retried" (ingestion.py, jobs.py retry parts, test_reupload_withdrawn.py, handoff doc).
   If `jobs.py` cannot be split cleanly, one commit for both is acceptable — say so in the report.
   Local tag `live-<YYYYMMDD>-b14` on the last commit. **No push.**
3. **Backup:** `pg_dump -Fc` → `C:\Users\jainm\reconbench-archive\live-db-<YYYYMMDD>-b14.dump` + roles (`pg_dumpall --roles-only --no-role-passwords`). Restore into a throwaway container (`--no-owner`), compare row counts of every public table with live (must be identical), remove the container.
4. **Rollback tags:** `docker tag reconbench-{setup,api,worker,ai-worker}:latest …:pre-b14`.
5. **Build:** `docker compose build setup api worker ai-worker`.
6. **Setup:** `docker compose run --rm setup` → revision must stay 0036.
7. **Swap:** `docker compose up -d api worker ai-worker`; wait for `GET http://127.0.0.1:8000/readyz` = 200.
8. **Container check (all three):** `grep -c run_vendor_rematch /app/backend/reconbench_app/jobs.py` ≥ 1, `grep -c InfrastructureRetryStrategy /app/backend/reconbench_app/jobs.py` ≥ 1, `grep -c DOCUMENT_COLLISION_ACTIVE /app/backend/reconbench_app/ingestion.py` ≥ 1, `ENGINE_VERSION = "0.1.14"`; no errors in the first 2 minutes of logs.
9. **Post-checks (read-only):** queue drains to 0; 0 new failed jobs; `ai_request`, `link`, `document`, `document_version` counts unchanged (nothing runs until a person acts); switch unchanged; Alembic 0036.
10. **Do not** run matching, upload, assign or reconcile on live. Those are the owner's clicks (below).
11. **Records:** decision-log entry (next free D-number after D118; D119/D120 stay reserved for the step-10 and PD-B1/B2 records, so use D121) with the gate numbers, commits, tag, backup, checks; KNOWN_ISSUES KI-012/KI-013 → "deployed, awaiting owner check"; PROJECT_STATUS Last verified / Tests / Active task.

## Stop conditions
Any gate failure; queue not empty at step 1; restore row counts differ; setup changes the revision; any counter in step 9 changes without a person's action; errors in the logs.

## Rollback
`docker tag reconbench-{api,worker,ai-worker,setup}:pre-b14 …:latest` then `docker compose up -d api worker ai-worker`. No schema or web change to undo. Restore the b14 dump only if data went wrong.

## Owner's checks afterwards (in the app)
1. **GRN re-upload (KI-013):** Ingestion → re-upload the failing GRN PDF (test file grn_b in the local test record). Expect: status Parsed within about a minute, 1 document found, no "Failed". It reappears as the earlier GRN (revived), not a new copy.
2. **Vendor re-match (KI-012):** Mapping → Unassigned → PO tab → the name-only service PO (test file po_a) → Assign to the manually created name-only vendor (or rename that vendor and save). Expect one `rematch_vendors` job; the PO (and the revived GRN, if its name matches) shows under that vendor; the PO/GRN of the ambiguous multi-vendor name stay Unassigned.
3. **Expected limit:** the test invoice inv08 will still have **no PO link** — that PO PDF was read with no lines or totals and the invoice has no readable PO number. Linking it needs a manual step (check Reconciliation → Manual Recon) or a better copy of the PO.
4. Afterwards, the agent (or Claude) reads back, read-only: file status, which GRN document was revived, vendor of the PO/GRN, link changes (expected: none for inv08), 0 new auto links, `ai_request` unchanged.

## Report back (for Claude's review)
Gate results with counts; commit hashes and tag; backup file + restore check; image/container checks; pre/post counter table; any deviation and why; `git status --short` at the end.
