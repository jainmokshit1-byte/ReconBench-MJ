# Step 7, phase 1: PD-5c measurement on beta (rules vs AI on the same scanned invoices)

Brief for an executing agent. It is self-contained: do not explore the repo beyond the files named here, and do not re-read `docs/accuracy-plan.md` §15 or the whole decision log.

## Goal
Measure, on up to 10 real scanned invoices uploaded to beta (the live stack in this folder), how the **rules reading** and the **AI reading** compare per field, scored against the owner's existing answer sheet. No application code changes. Beta ends exactly as it started, except for the uploaded test documents, the AI requests they made and the audit entries for the two switch changes.

## Hard rules (stop and ask the owner if any would be broken)
1. **Never open, list, copy, quote or hash anything in `work/`, `.env` or `deploy/secrets/`.** Only the owner runs code that reads `work/`.
2. **Gemini stays disabled.** 0 Gemini calls. If any AI setting names Gemini as provider, primary, extraction, assist or shadow reader, or provider fallback is on: STOP and report.
3. **Company AI server (vLLM) budget: at most 40 chat completions in total, plus at most 2 `GET /models`.** Log the budget (UTC time, the numbers, "0 Gemini") in your report **before** the owner turns the switch to AI, and wait for the owner's go in chat.
4. **The owner changes the "Scanned invoices" switch in Settings, not you.** Never type or ask for a password. You only verify it in the database.
5. **No changes to beta beyond this brief:** no migrations, no container rebuilds or restarts, no settings changes, no deletes, no restore (a restore is the owner's decision after the report). Every SQL you run on beta is in a `BEGIN READ ONLY; … ROLLBACK;` block.
6. **Real invoice data stays local:** the readings file goes only to `C:\Users\jainm\reconbench-archive\pd5c-<YYYYMMDD>\`; never print values from it, from the database rows of the test documents or from logs. Reports and docs contain counts only. Mask the company AI server address everywhere.
7. **No commits, no pushes, no branch switches, no installs.** No refactoring. Do not edit any file under `backend/reconbench_core`, `backend/reconbench_app`, `web/` or `backend/migrations`.
8. Windows host, Git Bash: prefix docker commands with `MSYS_NO_PATHCONV=1`; use `backend/.venv/Scripts/*.exe`. Heredocs break on an odd number of apostrophes: write longer SQL/scripts to files and run them.

## Facts you can rely on (verified 30 Oct 2026, read-only)
- Beta = the default compose project in this folder. Containers: `reconbench-postgres-1`, `reconbench-api-1`, `reconbench-worker-1`, `reconbench-ai-worker-1`, `reconbench-web-1`, `reconbench-minio-1`.
- Read-only SQL on beta without touching secrets:
  `MSYS_NO_PATHCONV=1 docker exec -i reconbench-postgres-1 sh -c 'psql -X -q -A -F " | " -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < file.sql`
- Baseline: `alembic_version` 0036; `ai_setting.scanned_invoice_reader` = `rules`, `version` 24, `updated_at` 2026-10-29 18:34 UTC; `ai_request` 113 rows (111 succeeded, 2 failed); `document` 545 rows, all `reading_state = 'ready'`; `field_evidence` with `origin = 'ai'`: 0; `procrastinate_jobs` open (`todo`/`doing`): 0; `audit_log` last `occurred_at` 2026-10-29 18:34 UTC; no scanned pages on beta before this test.
- Relevant tables: `ai_setting` (`scanned_invoice_reader`, `version`, `provider`, `primary_reader`, `extraction_provider`, `assist_provider`, `shadow_provider`, `allow_provider_fallback`, `reader_strategy`, `consent_fingerprint`), `ai_request` (`provider`, `request_state`, `subject_id`, `settings_version`), `ai_call_log` (one row per AI **request**, not per HTTP call; has `document_id`, `document_version_id`, `provider`), `document` (`reading_state`, `reading_failure_code`, `current_version_id`), `document_version` (`seq`, `extraction_method`, `reading_method`, `derived_from_version_id`, header columns, `extras`), `doc_line`, `field_evidence` (`origin`), `audit_log` (`occurred_at`, `action`), `procrastinate_jobs`. Run `\d <table>` before relying on any other column.
- How a scanned invoice is read with the switch on AI (verified in code):
  - The rules reading runs first and is saved unchanged as **version 1** (`backend/reconbench_app/ingestion.py:parse_file`; the switch is only consulted at its end in `_queue_vision_reading`). The document becomes `waiting`, so mapping, linking and reconciliation skip it (`mapping_service.py:_load_current`, `reading_state.ready_clause`).
  - The AI reading is appended as **version 2** (`IngestionService.append_ai_version`: `extraction_method = 'ai_first'`, `derived_from_version_id` = version 1), merged by `backend/reconbench_core/extraction/merge.py:merge_readings`. Version 2 becomes `current_version_id`, the document becomes `ready`. Per-field outcomes are in version 2's `extras` under `cross_check` (`agree`, `disagree`, `ai_only`, `ocr_kept`, `person_kept`, `ocr_only_line`); OCR header values under `ocr_reading`.
  - A failed reading leaves the document `failed` with `reading_failure_code`; it stays out of linking. Retry costs calls; "Use rules" costs none.
  - A previous synthetic 1-page test made 2 completions (full page + header band) and 1 `GET /models` for 1 `ai_call_log` row. A simulated 503 puts the server in a shared 30 s backoff (KI-004).
- Answer-sheet format (`scripts/accuracy_bench.py:load_truth`): `files[]` with `id`, `path`, `zone`; `sets[].documents[]` with `id`, `doc_type`, `sources[].file` (file ids), `header`, `lines`. The bench reads sample bytes from the samples folder and hashes them with SHA-256. Beta stores uploaded files by SHA-256. So **test documents are matched to the answer sheet by file SHA-256**: the owner must upload byte-identical copies of files that are in the answer sheet.

## Tasks

### T1. Read-only pre-checks (no owner action)
- Re-run the baseline queries above and report any difference. If the switch is not `rules`, an AI request is open, a job is open, or Gemini appears in any provider column: STOP.
- Report the `ai_provider` enum values and which one is active.
- From code only (`backend/reconbench_app/ai/vision.py`, `ai/vision_bands.py`, `ai/worker/openai_compat.py`, `ai/ledger.py`): report (a) the maximum number of chat completions one scanned document of N pages can make, including automatic retries (`attempt_no`) and band calls; (b) the per-call output token cap for `h6_vision`; (c) whether anything logs one line per completion that you can count without printing values or the server address. Define the counting method you will use. If you can't bound the completions per document from the code, STOP and report.

### T2. Backup with a proven restore (no owner action)
- `pg_dumpall --roles-only --no-role-passwords` and `pg_dump -Fc` of the beta database, run inside `reconbench-postgres-1`, copied to `C:\Users\jainm\reconbench-archive\live-db-<YYYYMMDD>-pd5c.dump` (+ `…-roles.sql`).
- Test restore in a throwaway `postgres:16.15-bookworm` container on another host port (no published port needed): apply the roles first, then `pg_restore`; compare exact per-table row counts (`count(*)` for every table in `public`, not `pg_stat` estimates) with beta. Only GRANT errors to roles the throwaway cluster lacks are acceptable; list them.
- Remove the throwaway container and prove it's gone. Note: uploaded files in MinIO are not in this backup (a DB restore would leave them as harmless orphans).

### T3. Scorer and dump tooling (offline, synthetic data only)
- `scripts/pd5c_dump.sql`: one read-only query that outputs one JSON document listing, for each test document (identified by the `document` ids from T4): its source file SHA-256, page count, `reading_state`, `reading_failure_code`, version 1 (rules) and version 2 (current, AI) header columns and lines, and version 2's `extras.cross_check`. Find the file-hash column with `\d` on the file table; don't guess.
- `scripts/pd5c_score.py`: `--readings <dump.json> --truth <path> --samples <folder> [--out <counts.json>]`. Hash each answer-sheet source file under `--samples`, match test documents by SHA-256, and for each header field and line amount count, per arm, `correct / wrong / missing` with n:
  - **rules** = version 1; **AI as the app uses it** = version 2; **AI alone** = the version 2 fields whose `cross_check` outcome is `agree`, `disagree` or `ai_only`.
  - Also count: fields where the AI and OCR agree but both are wrong; documents not matched, failed or deduplicated (listed by count, not name); and a Wilson 95% interval for each accuracy.
  - **Reuse** the bench's own value comparison/normalisation and Wilson helper from `scripts/accuracy_bench.py` (import them; don't write new matching rules). Decimal only, no floats in value comparison. Print and write **counts only**, never values, file names or paths.
- Unit test next to `backend/tests/unit/test_accuracy_bench.py` (new file `test_pd5c_score.py`) with a tiny synthetic answer sheet, synthetic dump and synthetic sample bytes in `tmp_path`: correct/wrong/missing, AI-alone selection, an unmatched document, a failed document, and a check that no value string appears in the output.
- Checks: `backend/.venv/Scripts/pytest.exe backend/tests/unit/test_pd5c_score.py -q`; `backend/.venv/Scripts/ruff.exe check` and `ruff format --check` on the new files only.

### T4. Controlled AI window on beta (owner actions in **bold**)
1. Ask the owner for the **page count of each scan** (numbers only) and confirm each is a byte-identical copy of a file in the answer sheet. Choose scans so that `sum(max completions per document from T1) ≤ 40`; if 10 don't fit, use fewer.
2. Write the budget log line in your report (UTC time, documents, pages, completion ceiling, ≤ 2 `GET /models`, 0 Gemini). **Owner gives the go.**
3. Record T0 (UTC). **Owner sets "Scanned invoices" to AI in Settings.** Verify: `scanned_invoice_reader = 'ai'`, `version` 25, one `ai.scanned_invoice_reader_change` audit row, consent fingerprint unchanged.
4. **Owner uploads one scan at a time.** After each: wait until the new document is `ready` or `failed` (poll the database read-only, at most every 30 s); record its `document` id, version count, failure code, and the running completion count (method from T1). Before the next upload, if `completions so far + ceiling(next) > 40`: stop uploading. A deduplicated upload (no new document) or a non-scanned document is excluded and reported. **No retry of a failed reading without the owner's go** (and only within the 40).
5. **Owner sets the switch back to rules.** Verify: `rules`, `version` 26, second audit row. Record T1 (UTC). The whole window should be under 45 minutes.

### T5. Post-checks on beta (read-only)
- Since T0: every new `ai_request`/`ai_call_log` row is for a test document and uses the active company provider; 0 Gemini; no open jobs; no new failed jobs other than failed readings you listed.
- For the test documents: version 1 exists and is unchanged (`extraction_method` not `ai_first`, created before version 2); version 2 has `derived_from_version_id` = version 1; 0 `field_evidence` rows with origin `ai` that are verified without a person; 0 automatic (non-suggested) links and 0 `Matched` cases involving a test document (find the link/case tables with `\d`; report the queries you used).
- Other documents: still 545 `ready`, `alembic_version` 0036.

### T6. Dump and hand over the scoring
- Run `scripts/pd5c_dump.sql` read-only with the test document ids; write the output to `C:\Users\jainm\reconbench-archive\pd5c-<YYYYMMDD>\readings.json`. Report only its size and the number of documents in it.
- Give the owner the exact command to run themselves (it reads `work/`), for example:
  `backend/.venv/Scripts/python.exe scripts/pd5c_score.py --readings C:\Users\jainm\reconbench-archive\pd5c-<YYYYMMDD>\readings.json --truth work/accuracy-truth/truth.json --samples work/accuracy-samples --out C:\Users\jainm\reconbench-archive\pd5c-<YYYYMMDD>\counts.json`
  The owner pastes the printed counts back to you.

### T7. Record (counts only)
- Add **D112** to `docs/decision-log.md` after D111, in its style: budget line, T0/T1, switch versions 25/26, documents and pages read, completions sent, Gemini 0, post-check results, the owner's pasted counts with n and intervals, "not decision-grade if n is small", and "live switch stays on rules until the owner decides".
- In `docs/claude/PROJECT_STATUS.md` update only: the `Last verified` line, the Active task lines and the PD-5c point under Next steps. Don't touch other docs.

## Acceptance (phase 1 is done when all hold)
- Backup taken and test-restored with identical per-table row counts.
- `pd5c_score.py` unit test passes; ruff clean on the new files.
- ≤ 40 completions, ≤ 2 `GET /models`, 0 Gemini, every request for a test document only.
- Switch back on `rules` (version 26) and verified; beta otherwise unchanged (0036, 545 other documents ready, no open jobs).
- Hard zeros on the test documents: 0 auto links, 0 `Matched`, 0 AI values accepted without a person.
- Owner's per-field counts recorded in D112 with n.

## Stop and report (don't work around)
Any baseline mismatch; Gemini anywhere; the completion count can't be bounded or counted; a 503/backoff storm; a document stuck `waiting` for more than 10 minutes; any value-bearing output you'd have to show to proceed; anything that needs a code change in the app.

## Report back (paste this filled in, nothing else)
```
PHASE 1 REPORT
T1 pre-checks: baseline match yes/no (diffs); active provider; enum values; max completions/doc formula; h6_vision output cap; counting method
T2 backup: files + sizes; restore row-count match yes/no (tables compared); GRANT errors; throwaway removed yes/no
T3 tooling: files added; pytest result (n passed); ruff check/format result
T4 window: budget line (UTC); T0/T1; switch versions seen; per document: id-prefix(8), pages, state, failure code, completions
T5 post-checks: each check with its count and the query file used
T6 dump: readings.json size, documents; scorer command given
T7 records: D112 added yes/no; PROJECT_STATUS lines changed
Owner counts (pasted): …
Deviations from this brief / open issues: …
Files changed (git status --short): …
```
