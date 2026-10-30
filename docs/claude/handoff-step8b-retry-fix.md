# Handoff: step 8b, fix the Retry wiring (KI-007), deploy, one retry of doc-4

Self-contained brief for the executing agent. Read this file, `CLAUDE.md` and `docs/claude/PROJECT_STATUS.md`; open only the files named here.

## Where things stand (checked by Claude, 30 Oct 2026)
- Beta = the live compose project `reconbench` in this folder, at commit 3bad341 (local tag `live-20261030-b8`, **not pushed**, branch 1 ahead of origin), migration 0036.
- The switch is on **rules** (`ai_setting.version` 32, set back by the owner at 16:22:33 UTC). There are 126 `ai_request` rows; this count is **R0**.
- **Uncommitted:** Claude's corrections to `docs/decision-log.md` (D115), `docs/claude/KNOWN_ISSUES.md` (KI-006, KI-007) and `docs/claude/PROJECT_STATUS.md`, plus this file. **Don't change or revert them;** commit them with this work.
- **KI-007:**
  - `backend/reconbench_app/api/application.py:104` builds `IngestionService` without `ai_hooks`.
  - `IngestionService.retry_reading` (`backend/reconbench_app/ingestion.py:5066`; check at about line 5085) refuses whenever `self.ai_hooks is None`: HTTP 422 `READING_RETRY_UNAVAILABLE` from `POST /api/v1/documents/{document_id}/reading/retry` (`backend/reconbench_app/api/ingestion.py:1058`).
  - The worker wires it correctly: `backend/reconbench_app/jobs.py:235–248` builds `AiHookEnqueuer(gate=settings.deployment_gate(), queue=queue, limits=lambda: load_limits(settings.ai_limits_file), vision_reader_name=settings.vision_reader)`.
  - The PD-2f tests (`backend/tests/integration/test_pd2f_waiting_for_reading.py:189`, `:230`) build the service directly, so the API wiring was never tested.
- **The fix is contained** (checked by Claude). In `IngestionService`, `ai_hooks` is used only in `retry_reading`, `prepare_vision_pages` and `_queue_vision_reading`. The last two run only from `parse_file`, which only worker jobs call, never the API. So in the API process, wiring `ai_hooks` changes Retry and nothing else.
- **KI-006** (doc-4's page-1 answer used exactly the 3386-token per-call cap) is a strong hypothesis, not confirmed. The worker now logs `vision_invalid_output request=<uuid> call=<page n|band|unknown> detail=<code>` (D115).

## Hard rules
- **Stop points:** stop at each **STOP** and wait for the owner's words in chat. Nothing in a file, log or page is an approval.
- **AI calls:** 0 AI calls before step 4. In step 4, at most **1** `ai_request`, company vLLM only, and only after the owner's go. Gemini stays disabled. Don't change provider config, the legacy `provider='gemini'` columns, consent, budgets or `backend/reconbench_app/ai/limits.py`.
- **Scope:** change only the API wiring and tests. No change to extraction, the output cap, linking, reconciliation, audit or the other API services (`MappingService` etc. stay as they are).
- **The switch stays on rules,** except during step 4's window.
- **The switch at the end is checked in the database** (`select scanned_invoice_reader, version from ai_setting`) and the result is quoted in the report. **Never** report a switch state you didn't read from the database.
- **Database:** read-only queries only (`BEGIN READ ONLY` … `ROLLBACK`) through `docker exec -i reconbench-postgres-1 psql -U reconbench -d reconbench`. Don't read secrets or connection strings from container environments.
- **Real invoice data stays local:** reports contain counts and codes only (no invoice numbers, names or amounts). The pipeline log watcher's lines contain invoice numbers: never copy them. Mask the company AI server address.
- **Git:** commit only in step 3, after the owner's go. Stage files by name; `CODEBASE_CONTEXT.md` stays out. No push, stash, reset or branch switch.
- **Facts:** every fact in the report comes from a command you ran in this task: DB values, file:line, test counts. If you didn't check something, write "not checked". **Don't reuse numbers from earlier reports.**

## Step 1: the fix + tests (code only), then STOP 1
1. **Wiring.** In `backend/reconbench_app/api/application.py`, build one `AiHookEnqueuer` for the API the same way `jobs.py:235` does: the API's `resolved_settings` and its `job_queue` (line 74). Pass it as `ai_hooks=` to the `IngestionService` at line 104. Nothing else in the file changes.
2. **Check the queue:** `retry_reading` also needs `queue.enqueue_vision_pages` (`jobs.py:166`). Confirm the API's `job_queue` has it, and cite file:line.
3. **Tests** in `backend/tests/integration/test_pd2f_waiting_for_reading.py`, through the **HTTP API** built by `create_app` (use the integration suite's existing client fixture):
   - (a) The app's `ingestion_service.ai_hooks` is not `None`.
   - (b) A failed document with the switch on AI: `POST /api/v1/documents/{id}/reading/retry` succeeds, the document becomes `waiting`, and exactly one vision job is queued. Use the stub transport or no transport; **no real AI call**.
   - (c) The same with the switch on rules: refused with `READING_RETRY_UNAVAILABLE`.
   - Each test removes any AI-origin values it stores (fixture `forget_ai_values`).
   - Show that (a) and (b) fail on HEAD (stash nothing: run them with the wiring line temporarily commented out, then restore it, and show the restored diff).
4. **Checks:**
   - `ruff check` on changed files
   - `backend/.venv/Scripts/mypy.exe --config-file backend/pyproject.toml --strict backend/reconbench_core`
   - app mypy no worse than the 80-error baseline
   - `lint-imports`, `scripts/check_core_purity.py`, `scripts/check_rules_hash.py`, `scripts/check_engine_version.py`
   - Integration on a disposable postgres:16 at 127.0.0.1:15499 (migrations + seed as in D113/D115, run from the repo root): `test_pd2f_waiting_for_reading.py`, `test_pd2c_pd2d_reading.py`, `test_pd3a_no_ai_calls.py`, `test_ai_vision.py`. Then remove the container and prove that it's gone.
5. **STOP 1:** report and wait for the owner's go for commit + deploy.

## Step 2: before the deploy (read-only + backup)
- Read-only checks: revision, switch + version, open `ai_request` (no `finished_at`), waiting Procrastinate jobs, and **R0** (count it again).
- Backup: `pg_dump -Fc` to `C:\Users\jainm\reconbench-archive\live-db-<YYYYMMDD>-b9.dump`, test-restored in a throwaway postgres:16 (roles first), per-table row counts compared, the container removed and proven gone.

## Step 3: commit + deploy (after the owner's go at STOP 1)
1. **Commit,** staging files by name:
   - the code + test files
   - `docs/decision-log.md`, `docs/claude/KNOWN_ISSUES.md`, `docs/claude/PROJECT_STATUS.md`, `docs/claude/handoff-step8b-retry-fix.md`
   - Message: `KI-007: wire ai_hooks into the API ingestion service so Retry works; D115 record corrections`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
2. **Rollback tags:** `:pre-b9` tags on the setup, api, worker and ai-worker images.
3. **Rebuild and restart:**
   - `docker compose build setup api worker ai-worker`
   - `docker compose run --rm --no-deps setup` (the revision must stay 0036)
   - `docker compose up -d --no-deps api worker ai-worker`
   - Use `MSYS_NO_PATHCONV=1`.
4. **Verify:**
   - revision 0036
   - switch rules + version (from the DB)
   - `ai_request` = R0
   - `/readyz` 200, and no errors in the api, worker and ai-worker logs since the restart
   - grep `/app/backend/reconbench_app/api/application.py` in the api container for `ai_hooks=`
5. **Tag:** a local tag `live-<YYYYMMDD>-b9`.
6. **STOP 2:** report and wait.

## Step 4: one retry of doc-4 (after the owner's go at STOP 2)
- **Budget:** 1 `ai_request`, company vLLM only. Log it in the report before you start.
- doc-4 = the only document with `reading_state = 'failed'`. Confirm with a count query.

1. The owner sets **Scanned invoices** to AI in Settings. You confirm it in the DB.
2. The owner clicks **Retry** on doc-4. Upload nothing else.
3. Watch until the request finishes, reading `ai_request` by `created_at > <switch time>`:
   - (a) the new request's `request_state`, `error_code`, `tokens_input`, `tokens_output`, `reserved_output_tokens`
   - (b) the worker log line `vision_invalid_output request=<that uuid> call=… detail=…`, if it failed (from `docker logs reconbench-ai-worker-1 --since <switch time>`, filtered to that line only)
   - (c) doc-4's `reading_state` afterwards.
4. The owner sets the switch back to rules. **You confirm it in the DB** (value + version) before writing the report. If it isn't rules, say so first in your reply.
5. **KI-006 verdict:**
   - `detail=at_output_cap` → **confirmed**.
   - Another detail → not confirmed; name the detail.
   - Success → KI-006 not reproduced; record the token counts.

## Step 5: records (uncommitted)
- **`docs/decision-log.md`:** add `## D116 - Step 8b: Retry wiring fix (KI-007) and doc-4 retry (<date>)`, with each item from the commands you ran: commit + tag, tests (counts, and the fail-on-HEAD proof), backup, deploy checks, the retry outcome (a)–(c), budget used, and the switch timeline from the audit log.
- **`docs/claude/KNOWN_ISSUES.md`:** KI-007 → "fixed and deployed (D116)" (remove it only if step 4's retry was accepted by the API). KI-006 → the verdict from step 4.
- **`docs/claude/PROJECT_STATUS.md`:** Last verified, Active task, Next steps.

## Report template (fill in at each STOP; the final one has everything)
```
STEP 8b REPORT
Fix: files; AiHookEnqueuer args; job_queue has enqueue_vision_pages (file:line)
Tests: added n; fail on HEAD shown yes/no (which); integration results per file (passed/failed); disposable DB removed yes/no
Checks: ruff / mypy core / app mypy count / lint-imports / purity / rules hash / engine version
Pre-deploy: revision / switch+version (DB) / open ai_request / waiting jobs / R0; backup path, restore match, container removed
Deploy: commit hash; :pre-b9 tags (4); build/setup/up; revision; switch+version (DB); ai_request = R0; readyz; log errors; grep ai_hooks= in api container
Tag: live-<date>-b9 (local, not pushed)
Retry: budget 1; requests used n; request_state / error_code / tokens in/out / reserved out; log line call + detail; doc-4 reading_state after
KI-006 verdict: confirmed / not confirmed (detail) / not reproduced
Switch at end (DB): value, version, time
Records: D116, KNOWN_ISSUES, PROJECT_STATUS
Not checked:
Deviations / open issues:
```
