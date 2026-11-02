# Handoff: KI-013 — re-uploading a removed PDF fails with a duplicate-key error

For: the implementing agent (Antigravity). Written by Claude, 2 Nov 2026. Claude reviews the final report.
Read first: `CLAUDE.md` (rules), `docs/claude/KNOWN_ISSUES.md` KI-013, this file. Do not read `work/`, `.env`, `deploy/secrets/`.

## Ground rules (must follow)
- Do **not** touch live: no live DB writes, no container rebuild, no deploy, no commit, no push. Live = containers `reconbench-*` and DB on 127.0.0.1:15432.
- Read-only live queries are allowed only with `PGOPTIONS="-c default_transaction_read_only=on"`.
- Test only on a throwaway PostgreSQL (recipe below). Never point tests at live.
- No AI provider calls (no Gemini, no company vLLM).
- Do not change matching/reconciliation rules (`backend/reconbench_core/{align,mapping,normalize,recon,schema}`). This fix is app-only (`backend/reconbench_app`).
- Leave the uncommitted KI-012 change (vendor re-match: `jobs.py` `run_vendor_rematch`/`enqueue_vendor_rematch`, `mapping_service.py` `_enqueue_vendor_rematch`, `tests/unit/test_jobs.py`, `tests/integration/test_vendor_rematch.py`) exactly as it is. Do not revert or reformat it. Keep your diff separate.
- Never write real invoice data (names, amounts, GSTINs) into code, tests, docs or the report. Use synthetic values.
- Windows host: use `backend/.venv/Scripts/*.exe`. In Git Bash use `MSYS_NO_PATHCONV=1` for docker commands with `/tmp` or `/app` paths.

## The bug (root cause found by Claude, verified in code + live DB)
1. A scanned GRN PDF was uploaded and removed 4 times before. Each earlier parse created a new `document` row; all 4 are now withdrawn (`withdrawn_at` set) and all share the file's identity (`document_identity.source_sha256` = file SHA, `source_locator = 'layout'`).
2. Re-upload of the same bytes reactivates the old `source_file` row (`ingestion.py:receive_file`) and parses again.
3. In `backend/reconbench_app/ingestion.py` (around lines 2849–2911, the upsert of a parsed document) the code finds earlier documents by file identity (`candidate_ids`) and reuses one **only when exactly 1 candidate** exists (`if len(candidate_ids) == 1`). With 4 withdrawn candidates, `existing` stays `None`, so it plans a new document.
4. Around lines 2952–2967: the natural key is already owned, so it switches to `document_identity_collision_key(...)` (`backend/reconbench_app/document_identity.py:291`). That key is **deterministic**, and the previous upload of the same file (1 Nov) already created a document with exactly that key. The INSERT then violates the unique index `document_natural_uq (workspace_id, natural_key)` (migration 0005).
5. `IntegrityError` is a `sqlalchemy.exc.DBAPIError`, which is in `_RETRYABLE_ERRORS` (`backend/reconbench_app/jobs.py:53`), so the parse job retries 5 times and ends `failed`. Owner reproduced it again on 2 Nov.

## Required fix (two parts, small and safe)
**A. Reuse the most recent removed copy.** When the identity lookup returns more than one candidate and **every** candidate is withdrawn, choose one deterministically — the most recently withdrawn (`withdrawn_at` desc, then `id` desc) — and continue exactly as the existing single-candidate path does (that path already revives a withdrawn document: see the `withdrawn_at=None, withdrawn_by=None` updates around lines 3094 and 3215). Keep the other withdrawn copies untouched (history).
- If any candidate is **active** (not withdrawn), keep today's behaviour (do not merge active documents).
- Also guard step 4: before inserting with the collision key, check whether that key is already owned. If its owner is a withdrawn document of the same doc type, reuse that owner as `existing` (same revive path). If the owner is active, do not crash: hold/mark the file with a clear error code instead (follow the existing `IngestionFailure` / file-status pattern in this module; pick an existing code if one fits, otherwise add one and its plain-language text where error codes are listed). Never create a second active document for the same file.

**B. Stop retrying unique-constraint errors.** A unique violation is not an infrastructure failure. Make the parse/ingest tasks not retry `sqlalchemy.exc.IntegrityError` (e.g. exclude it from `_RETRYABLE_ERRORS` handling, or catch it in the task and record a non-retryable failure). The file must end `failed` with a stated reason on the first attempt, not after 5 retries. Keep retries for real infrastructure errors (connection loss, object store).

Out of scope: PO/GRN number duplicates between Tally XML and PDF copies (KNOWN_ISSUES M-3), vendor matching, any UI change.

## Tests to add (throwaway DB, synthetic data)
Put them in a new file `backend/tests/integration/test_reupload_withdrawn.py`, reusing helpers from `tests/integration/test_phase4_task_411_413.py` (`scratch_engine`, `_seed_workspace`, `_Queue`, `_upload_csv` pattern, `ingestion.create_batch`) and the removal API used elsewhere (search for `remove_file`/`file.remove`). Scenarios:
1. Upload → remove → upload the same bytes → succeeds; one active document; it is the revived earlier document (same id) with a new version or unchanged-skip as the existing path decides.
2. Upload → remove repeated 3+ times, then upload again → succeeds (this is the live bug). Exactly one active document; older withdrawn copies stay withdrawn.
3. A collision-key owner that is withdrawn is revived, not duplicated (construct the state directly if hard to reach through uploads).
4. Active document already holding the key → no crash, file ends with the stated error code, no second active document.
5. Non-retryable: an `IntegrityError` during parse marks the file failed on attempt 1 (unit test with a fake service/queue is fine).
6. A brand-new different file is unaffected (normal ingestion still creates a new document).
Use CSV or the synthetic PDF fixtures already in `scripts/vision_fixtures.py` / tests; for PDF identity with `source_locator='layout'` prefer the existing synthetic scanned-PDF helpers.

## Checks to run (all must pass; report numbers)
- `backend/.venv/Scripts/ruff.exe check` and `ruff.exe format --check` on touched files.
- `cd backend && .venv/Scripts/mypy.exe --strict` (core) and `.venv/Scripts/mypy.exe reconbench_app` (baseline 80 errors; must not increase).
- `cd backend && .venv/Scripts/lint-imports.exe`; `backend/.venv/Scripts/python.exe scripts/check_core_purity.py`; `scripts/check_rules_hash.py` (must be unchanged).
- Offline suite: `backend/.venv/Scripts/pytest.exe backend/tests/unit backend/tests/parsers backend/tests/property backend/tests/scenarios -q` (last: 1549 passed, 22 skipped).
- Integration on the throwaway DB, one file per pytest call: the new test file, `test_ingestion.py` (4), `test_vendor_rematch.py` (2), `test_ac4_vendor_hygiene.py` (6), `test_pd_ledger_scopes.py` (3), `test_phase4_task_411_413.py` (3), `test_page_split.py` (2 known failures on HEAD, KI-003 — report if the count changes).

Throwaway DB recipe (from `docs/claude/handoff-step6.md`):
```
docker run -d --rm --name rb-test-pg -e POSTGRES_DB=reconbench_phase2_ci -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=phase2-ephemeral-ci-only -p 127.0.0.1:15499:5432 postgres:16.15-bookworm
export RB_ENV=ci DB=postgresql+psycopg://postgres:phase2-ephemeral-ci-only@127.0.0.1:15499/reconbench_phase2_ci
export RB_MIGRATOR_DATABASE_URL=$DB RB_DATABASE_URL=$DB RB_PHASE2_TEST_DATABASE_URL=$DB
# plus the four RB_BOOTSTRAP_* values from .github/workflows/ci.yml (~line 263)
backend/.venv/Scripts/alembic.exe -c backend/alembic.ini upgrade head
backend/.venv/Scripts/python.exe scripts/seed_database.py
(cd backend && .venv/Scripts/procrastinate.exe --app reconbench_app.db.procrastinate_schema.app schema --apply)
```
Remove the container afterwards (`docker rm -f rb-test-pg`).

## Replay on a copy of live (proof on the real case, still throwaway)
1. Read-only dump: `MSYS_NO_PATHCONV=1 docker exec reconbench-postgres-1 sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > C:/Users/jainm/reconbench-archive/live-db-<date>-grn-test.dump` (+ roles: `pg_dumpall --roles-only --no-role-passwords`).
2. Restore into a throwaway container on port 15498 (`--no-owner`), never into live.
3. With the new code, call `IngestionService(...).parse_file(<the failing GRN's source_file id>)` against the copy (object store: copy the file's bytes from MinIO read-only, or use a `LocalObjectStore` holding those bytes in a temp folder that you delete afterwards).
4. Expected: parse succeeds; exactly one active GRN document for that file (a revived earlier copy); 0 new active duplicates; no other document changed (diff active documents and versions before/after).
5. Delete the container and any temp copy of the file.

## Report to hand back (to Claude for review)
Plain text, no real values:
1. Files changed (paths) and a 2–3 line summary per file.
2. The exact code paths changed (function names, line ranges).
3. Each test added: name, what it proves, result.
4. Every check command with its result (counts), including the integration list above.
5. Replay result: before/after counts of active documents and versions, which document was revived, any other change (must be none).
6. Anything you could not do or did differently, and why.
7. `git status --short` and `git diff --stat` at the end (no commit).
