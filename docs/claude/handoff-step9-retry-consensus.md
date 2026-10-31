# Handoff: step 9, make a person's Retry finish (KI-008, KI-009), deploy, one retry of doc-4

Self-contained brief for the executing agent. Read this file, `CLAUDE.md` and `docs/claude/PROJECT_STATUS.md`. Open only the files named here, unless a step tells you to search.

## 1. Goal
The **Retry** button on a failed scanned invoice must work end to end. Retry asks the AI again, and the fresh answer then goes through the same consensus step as a first reading. The result is a new AI version: current, unverified and "ready". It must not fail early, and it must not be lost.

Today a Retry spends an AI call and the invoice stays "failed". Two bugs cause this. KI-007 (the API refused Retry) is already fixed and deployed in b9.

## 2. Where things stand (checked by Claude, 31 Oct 2026)
- **Beta:** the live compose project `reconbench` in this folder, at commit `501dd4e` (local tag `live-20261030-b9`, **not pushed**), migration 0036.
- **Switch:** on **rules** (`ai_setting.version` 34). There are 127 `ai_request` rows. Count them again in step 2 and call that count **R0**.
- **doc-4:** the only document with `reading_state = 'failed'` (`reading_failure_code = 'invalid_output'`).
- **Uncommitted:** Claude's records for D116 in `docs/decision-log.md`, `docs/claude/KNOWN_ISSUES.md` (KI-007 removed, KI-008 and KI-009 added) and `docs/claude/PROJECT_STATUS.md`, plus this file. **Don't change or revert them** except as step 5 says. Commit them with this work.
- **`CODEBASE_CONTEXT.md`** is untracked and stays out of every commit.

## 3. The two bugs (cause checked by Claude in code and on beta)

### KI-008: the Retry answer never reaches consensus (confirmed)
1. `reconbench_app/ai/worker/hooks/vision.py:123` sets `cacheable=request.get("requested_by") is None`. This is deliberate (D109): a person's Retry must ask the provider again and never get a cached answer.
2. `reconbench_app/ai/worker/runner.py:244–262` sets `cache_key` (and does the cache lookup) only when `prepared.cacheable`. So a Retry request keeps `cache_key = NULL`.
3. `runner.py:500` stores the answer in `ai_cache` only when `cache_key is not None`. So a Retry's valid answer is **never stored anywhere**.
4. Two places read the answer back **only** from `ai_cache`, by `ai_request.cache_key`:
   - consensus: `reconbench_app/ai/consensus.py:570` (it requires `cache_key` to be set) and `:624–628`. `_load` returns `None`, and `_process` ends the run as `stale` (`:442`).
   - the document's AI suggestion view: `reconbench_app/ingestion.py:5470–5478`. It shows `expired`.
5. **On beta (D116):** the Retry request finished `succeeded`, with `apply_outcome = 'applied'` and a null `cache_key`. Its consensus run is `stale`, with a null `result_version_id`. It is the only one of 53 `h6_vision` requests with `requested_by` set, and the only stale consensus run of 11.

### KI-009: the periodic sweep re-fails a retried document with its old failure (strong hypothesis; your test must prove it)
1. Retry (`ingestion.py:5066–5117`) sets the document to `waiting` and queues `prepare_vision_pages`, both in one transaction.
2. `prepare_vision_pages` (`ingestion.py:2039–2102`) renders the pages (11 s on beta). Only then does it create the new `ai_request` row (`hooks.vision_read`, about line 2088).
3. In that gap, the old failed request is still the **latest** `h6_vision` request for the current version.
4. `reading_state._FAIL_FINISHED` (`reconbench_app/reading_state.py:150–182`) fails any `waiting` document whose latest `h6_vision` request ended badly. Called with no `request_id`, it checks **every** finished request.
5. Exactly one caller uses that form: the AI worker's periodic sweep, `ledger.sweep_requests` (`reconbench_app/ai/ledger.py:569`). The other callers (`ledger.py:378`, `:408`, `:472`) pass the `request_id` they just finished.
6. **On beta (D116):**
   - Retry at 19:04:42 UTC (audit: failed/`invalid_output` → waiting).
   - doc-4 `updated_at` 19:04:52.80, back to failed with the old code.
   - New request created at 19:04:53.78.
7. The existing test `test_an_older_failed_attempt_never_fails_a_retried_reading` (`backend/tests/integration/test_pd2f_waiting_for_reading.py:315`) runs the global form only **after** the new request exists, so it misses the gap.

## 4. The decided design (don't choose another; if it can't work, STOP and report why)

### Fix A (KI-008): a Retry stores its fresh answer under a key only it uses
- **Why this design:**
  - It keeps D109: a Retry never reads the cache.
  - It never overwrites or reuses a shared cache row. `ai_cache.store` uses `ON CONFLICT DO NOTHING`, so storing under the normal content key could leave an **older** answer in place, and consensus would then read the old answer. That would be a wrong value, and a hard-zero risk.
  - Consensus (`consensus.py`) and the review view (`ingestion.py:5473`) need **no change**. Both read by `ai_request.cache_key`, which will now point at this request's own answer.
- **Changes:**
  1. In `reconbench_app/ai/cache.py`, add:
     ```python
     def retry_cache_key(content_key: str, request_id: UUID) -> str:
         """A person's Retry (PD-2f) never reads the cache; its fresh answer is stored
         under a key only this request uses, so consensus can read it (KI-008)."""
         return _digest({"content_key": content_key, "key_version": "retry-1", "request_id": str(request_id)})
     ```
     The result is 64 lowercase hex characters, which satisfies the `d_hash` domain (`migrations/versions/0001_setup.py:23`).
  2. In `runner.py`, right after the existing `if prepared.cacheable and request["hook"] is not None:` block (`:245`), add an `elif` for exactly one case: `request["hook"] == VISION_HOOK and request["requested_by"] is not None`. Then:
     - set `cache_key = ai_cache.retry_cache_key(ai_cache.request_cache_key(request, digest), request_id)`
     - write it to the request row: `sa.update(ledger.REQUESTS).where(ledger.REQUESTS.c.id == request_id).values(cache_key=cache_key)` in its own `self.engine.begin()` block, the same way the cacheable branch writes it
     - **No `ai_cache.lookup`** in this branch.
     - Leave `job_id` handling as it is today for non-cacheable requests.
     - Add a one-line comment: "KI-008: a person's Retry never reads the cache (D109) but keeps its answer for consensus".
  3. The existing store at `runner.py:500` then stores the answer unchanged. Don't edit it.
- **Leave alone:**
  - `consensus.py` (its `cache_key is None` guard stays)
  - `ingestion.py`
  - `hooks/vision.py`
  - the cacheable branch
  - the key for automatic reads
  - `limits.py` and the output cap (KI-006 is separate)
  - every non-vision hook, and every non-cacheable request without `requested_by`: these keep today's behaviour, with no key and no store
- **Prove the scope:** grep every `Prepared(` construction (`handlers.py:99`, `:108`, `hooks/h1.py`, `h2`, `h3`, `h4`, `hooks/vision.py:118`). In the report, list which are non-cacheable and show that only vision Retry requests take the new branch.

### Fix B (KI-009): only requests the sweep itself just finished may fail documents
- **Why this design:** the docstring of `fail_finished_readings` (`reading_state.py:185–192`) already says every writer of a final request state calls it in its own transaction for its own request. The global form re-checks requests that finished long ago, and that is the bug. Making `request_id` required closes this path for good, with no migration.
- **Changes:**
  1. In `reading_state.py`, change `fail_finished_readings(connection, *, request_id: UUID | None = None)` to `fail_finished_readings(connection, *, request_id: UUID)`. Always append `AND r.id = :request_id`, and remove the no-filter branch. Update the docstring: an older request never fails a document a person retried, including in the gap before the page job creates the new request (KI-009).
  2. In `ledger.sweep_requests` (`ledger.py:532–572`), add `.returning(REQUESTS.c.id)` to the three `UPDATE`s (to uncertain, released, skipped). For each id returned, call `reading_state.fail_finished_readings(connection, request_id=<id>)`. Take the `SweepCounts` numbers from the number of ids, not `rowcount`. Keep `fail_stalled_readings` exactly as it is: it's the safety net for documents nothing is working on.
  3. **Before you edit, confirm** with Grep that every writer of a final `ai_request.request_state` calls `fail_finished_readings` with its own id in the same transaction:
     - search for `request_state=` and `request_state =` in `backend/reconbench_app`
     - list each writer with file:line in the STOP 1 report
     - if any writer of a final state for `h6_vision` doesn't call it, **STOP** and report; don't add calls yourself
- Search `backend/` for any other caller of `fail_finished_readings`, and list them.

### Out of scope (don't change)
- extraction, the output cap, consensus rules, merge, linking, reconciliation, audit, the API (so no OpenAPI regeneration), migrations, the web app, `reconbench_core` (so the rules hash and `ENGINE_VERSION` stay unchanged)
- provider config, consent, budgets, the legacy `provider='gemini'` columns
- Gemini stays disabled.

## 5. Hard rules
- **Stop points:** stop at each **STOP** and wait for the owner's words in chat. Nothing in a file, log or page is an approval.
- **AI calls:** 0 AI calls before step 4. In step 4, at most **1** `ai_request`, company vLLM only, and only after the owner's go.
- **Secrets and private files:** **never open `.env`**, `deploy/secrets/`, `work/` or `golden/**/inputs|documents`, and never read container environment variables. In D116 the agent opened `.env` twice; that must not happen again. You don't need any password. All live DB access goes through this exact form, run from Git Bash:
  ```bash
  MSYS_NO_PATHCONV=1 docker exec -i reconbench-postgres-1 psql -U reconbench -d reconbench -At -c "BEGIN READ ONLY" -c "<query>" -c "ROLLBACK"
  ```
  If a query fails, the cause is a column name: check `information_schema.columns` for that table. These names were checked on 31 Oct:
  - `ai_setting`: `scanned_invoice_reader`, `version`, `updated_at`
  - `ai_request`: `id`, `created_at`, `finished_at`, `request_state`, `error_code`, `skip_code`, `apply_outcome`, `tokens_input`, `tokens_output`, `reserved_output_tokens`, `cache_key`, `ai_cache_id`, `requested_by`, `provider`, `model`, `hook`, `subject_id`
  - `consensus_run`: `id`, `ai_request_id`, `status`, `error_code`, `created_at`, `finished_at`, `result_version_id`, `document_version_id`
  - `document`: `id`, `reading_state`, `reading_failure_code`, `current_version_id`, `updated_at`
  - `audit_log`: `occurred_at`, `action`, `target_type`, `before_state`, `after_state` (there is **no** `created_at`)
  - `ai_cache`: `cache_key`, `created_at`, `hit_count`
- **Database:** read-only queries only on the live DB.
- **Real invoice data stays local:** reports contain counts, codes, ids and times only (no invoice numbers, names, amounts, GSTINs or file names). Mask the company AI server address. Never copy pipeline log lines that contain invoice data.
- **Files:**
  - Edit docs with the Edit tool, keeping each file's line endings.
  - Don't create helper scripts inside the repo; temporary files go outside it and get deleted.
  - Leave no files in containers.
- **Git:** commit only in step 3, after the owner's go. Stage files by name. No push, stash, reset or branch switch.
- **Facts:** every fact in a report comes from a command you ran in this task. If you didn't check something, write "not checked". Don't reuse numbers from earlier reports.

## 6. Step 1: code + tests, then STOP 1
1. **Prove the bugs first.** Write the new tests below, then run them on the unchanged code. Show that T1 and T3 fail on HEAD, and quote the failing assertion. Stash nothing.
2. Make Fix A and Fix B.
3. **Tests.** Put them in `backend/tests/integration/test_pd2f_waiting_for_reading.py` and reuse its helpers: `_failed_scan`, `_runner`, `_answer`, `_etag`, `_reading`, `world.service.prepare_vision_pages(..., requested_by=world.user_id)`. For a valid vision answer and a consensus run, copy the pattern of `test_ai_vision.py:647–735` (`test_the_ai_reading_becomes_the_current_version_with_crop_provenance`): `recorded_response()`, `enqueue_consensus_run` with a `defer_job` list, `ConsensusService(...)._process(...)`, and `monkeypatch` of `consensus_app.reread_vision_crops_isolated`. Each test uses the `forget_ai_values` fixture. No real AI call: `httpx.MockTransport` only.
   - **T1, a Retry finishes (KI-008; must fail on HEAD).** Failed scan (first attempt against `_down`), then a Retry through the HTTP API, then `prepare_vision_pages` with `requested_by`, then the runner with a valid answer and the consensus enqueue, then `_process`. Assert all of these:
     - `_process(...) == "succeeded"`
     - the document is `("ready", None)` with a new current version
     - the consensus run has a non-null `result_version_id`
     - the Retry request has a non-null `cache_key` and a null `ai_cache_id`
     - an `ai_cache` row exists under exactly that key
     - the new version's AI fields are unverified, exactly as the PD-2e test asserts for a first reading
   - **T2, a Retry never uses a cached answer, and consensus reads the fresh one.** After the failed first attempt (whose `cache_key` K is set even though the call failed), insert one synthetic `ai_cache` row under K with a response that differs from the server's answer. Then Retry and run as in T1. Assert:
     - the MockTransport server received the Retry's calls (count them)
     - the Retry request's `ai_cache_id` is null
     - its `cache_key` is not K
     - the seeded row's `hit_count` is still 0
     - consensus succeeded using the Retry's own key
   - **T3, the sweep in the gap (KI-009; must fail on HEAD).** Failed scan, Retry through the API, then **before** `prepare_vision_pages`, call `ledger.sweep_requests(engine, now=datetime.now(UTC), queue_timeout_seconds=3600)`. The document must still be `("waiting", None)`.
   - **T4, the sweep still fails a document for a request it finishes itself.** A waiting document whose `h6_vision` request is stuck in `sending` with `lease_expires_at` in the past. After `sweep_requests`, the request is `uncertain` and the document is `failed` with that request's code. First check whether an existing test already covers this (`tests/integration/test_ai_runner.py:503` covers the request only). Add T4 if nothing covers the document.
   - **Existing tests:**
     - `test_retry_asks_the_active_provider_again_without_the_cache` (`:204`) asserts `cache_key is None` for the Retry request (`:241`). That assertion encoded the bug. Replace only that assertion: the Retry's `cache_key` differs from attempt 1's, and its `ai_cache_id` is null. Keep everything else in the test.
     - `test_an_older_failed_attempt_never_fails_a_retried_reading` (`:315`) calls `fail_finished_readings(connection)` with no id. Change it to call `ledger.sweep_requests(...)` and assert the same outcome.
     - Change no other existing test. If another test fails, stop and report it; don't edit it.
4. **Checks**, from the repo root:
   - `ruff check` and `ruff format --check` on the changed files
   - `backend/.venv/Scripts/mypy.exe --config-file backend/pyproject.toml --strict backend/reconbench_core`
   - app mypy: no worse than the 80-error baseline, and list any error in the changed files
   - `lint-imports`, `scripts/check_core_purity.py`, `scripts/check_rules_hash.py` (unchanged), `scripts/check_engine_version.py` (no bump needed: no rule path changes)
   - The offline suite: `backend/.venv/Scripts/pytest.exe backend/tests/unit backend/tests/parsers backend/tests/property backend/tests/scenarios -q`. The last run was 1502 passed, 22 skipped. Report your counts.
   - **Integration** on a disposable `postgres:16` at 127.0.0.1:15499. The database name **must** start with `reconbench_phase2_` (the guard at `test_ai_vision.py:115`). Set up as in D116 (env vars as in `.github/workflows/ci.yml:258–266`; `alembic -c backend/alembic.ini upgrade head`; `scripts/seed_database.py`; the procrastinate schema). Run these files and report passed/failed for each:
     - `test_pd2f_waiting_for_reading.py`
     - `test_ai_vision.py`
     - `test_ai_runner.py`
     - `test_pd2c_pd2d_reading.py`
     - `test_pd3a_no_ai_calls.py`
     - `test_ai_h2.py`, `test_ai_h3.py`, `test_ai_h4.py`
     - `test_ai_h1.py`: its known failure is KI-003; report any other failure.
   - Remove the container and prove that it's gone.
5. **STOP 1:** report (template in section 11) and wait for the owner's go for commit + deploy.

## 7. Step 2: before the deploy (read-only + backup)
- Read-only checks:
  - revision (0036)
  - switch + version
  - open `ai_request` (no `finished_at`): must be 0
  - waiting Procrastinate jobs: must be 0
  - **R0** (`count(*) from ai_request`)
  - doc-4: the `failed` count = 1, and its `reading_failure_code`
- Backup:
  - `pg_dump -Fc` to `C:\Users\jainm\reconbench-archive\live-db-<YYYYMMDD>-b10.dump`, plus a roles dump
  - test-restore it in a throwaway `postgres:16` (roles first)
  - compare row counts per table
  - remove the container and prove that it's gone

## 8. Step 3: commit + deploy (after the owner's go at STOP 1)
1. **Commit,** staging files by name:
   - the code + test files
   - `docs/decision-log.md`, `docs/claude/KNOWN_ISSUES.md`, `docs/claude/PROJECT_STATUS.md`, `docs/claude/handoff-step9-retry-consensus.md`
   - Message: `KI-008/KI-009: a person's Retry keeps its answer for consensus; the sweep re-checks only requests it finishes; D116 records`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
2. **Rollback tags:** `:pre-b10` on the setup, api, worker and ai-worker images.
3. **Rebuild and restart** (use `MSYS_NO_PATHCONV=1`):
   - `docker compose build setup api worker ai-worker`
   - `docker compose run --rm --no-deps setup` (the revision must stay 0036)
   - `docker compose up -d --no-deps api worker ai-worker`
4. **Verify:**
   - revision 0036
   - switch rules + version (from the DB)
   - `ai_request` = R0
   - `/readyz` 200, and no errors in the api, worker and ai-worker logs since the restart
   - grep inside the ai-worker container for `retry_cache_key` in `/app/backend/reconbench_app/ai/worker/runner.py`, and for `request_id: UUID)` in `/app/backend/reconbench_app/reading_state.py`
5. **Tag:** a local tag `live-<YYYYMMDD>-b10`.
6. **STOP 2:** report and wait.

## 9. Step 4: one retry of doc-4 (after the owner's go at STOP 2)
- **Budget:** 1 `ai_request`, company vLLM only. Log it in the report before you start.
1. The owner sets **Scanned invoices** to AI in Settings. Confirm it in the DB (value + version) and note the switch time T.
2. The owner clicks **Retry** on doc-4, once, and uploads nothing else.
3. **KI-009 live check:** within the first minute after the Retry, read doc-4's `reading_state` **twice**, about 20 s apart, while the new request is not finished. Both reads must be `waiting`. Quote both times.
4. **Watch until it finishes,** reading by `created_at > T`:
   - (a) the new `ai_request`: `request_state`, `error_code`, `apply_outcome`, `tokens_input`, `tokens_output`, `reserved_output_tokens`, `cache_key IS NULL`, `ai_cache_id IS NULL`, `requested_by IS NULL`
   - (b) its `consensus_run`: `status`, `error_code`, `result_version_id IS NULL`
   - (c) doc-4's `reading_state`, `reading_failure_code`, and whether `current_version_id` changed
   - (d) any `vision_invalid_output request=<that uuid>` line: `docker logs reconbench-ai-worker-1 --since <T>`, filtered to that line only
5. **If doc-4 became ready with an AI version, run the hard-zero checks.** Use the same read-only checks D114 used for S8/S9 (find them in `docs/decision-log.md` under D114), and report counts only:
   - every AI-origin field on doc-4's new version is unverified
   - 0 links auto-confirmed on doc-4's unverified AI fields
   - 0 `Matched` cases resting on them
6. The owner sets the switch back to rules. **Confirm it in the DB** (value + version) before writing the report. If it isn't rules, say so first in your reply.
7. **Verdicts:**

   | What happened | KI-008 | KI-009 | KI-006 |
   |---|---|---|---|
   | request succeeded + applied, consensus `succeeded`, doc-4 `ready` | fixed on beta | fixed on beta if step 3 read `waiting` twice | not reproduced |
   | request succeeded + applied, consensus not `succeeded` | **not fixed**: quote status + error_code, no further retry | from step 3 | not reproduced |
   | request failed or `discarded_invalid`, with `detail=at_output_cap` | not tested on beta | from step 3 | **confirmed** |
   | request failed or `discarded_invalid`, with another detail | not tested on beta | from step 3 | not confirmed (name the detail) |

   Never make a second request, whatever the outcome.

## 10. Step 5: records (uncommitted)
- **`docs/decision-log.md`:** add `## D117 - Step 9: Retry reaches consensus (KI-008) and the sweep gap (KI-009) (<date>)`. Each item comes from the commands you ran:
  - commit + tag
  - the fail-on-HEAD proof (T1, T3)
  - test counts per file
  - the offline suite counts
  - backup
  - deploy checks
  - the step 4 results (a)–(d), the KI-009 live check, the hard-zero counts
  - budget used
  - the switch timeline from `audit_log`
- **`docs/claude/KNOWN_ISSUES.md`:**
  - KI-008: if the verdict is "fixed on beta", remove it; otherwise mark it "fixed in code and deployed (D117), not yet seen on beta".
  - KI-009: move it to Confirmed with the T3 fail-on-HEAD evidence. Then remove it if fixed on beta; otherwise mark it fixed in code.
  - KI-006: the verdict from the table.
- **`docs/claude/PROJECT_STATUS.md`:**
  - Last verified (commit, tag, switch + version)
  - the "Waiting for reading" component line (Retry status)
  - the integration test counts
  - Active task, Next steps

## 11. Report template (fill in at each STOP; the final one has everything)
```
STEP 9 REPORT
Fix A: files + lines; Prepared( list with cacheable value each; branch condition
Fix B: files + lines; final-state writers (file:line, calls fail_finished_readings with id yes/no); other callers found
Fail on HEAD: T1 (assertion) / T3 (assertion)
Tests: new n; changed existing (names + what changed); per-file passed/failed; offline suite counts; disposable DB removed yes/no
Checks: ruff / ruff format / mypy core / app mypy count / lint-imports / purity / rules hash / engine version
Pre-deploy: revision / switch+version (DB) / open ai_request / waiting jobs / R0 / doc-4 state; backup path + size, restore match, container removed
Deploy: commit hash; :pre-b10 tags (4); build/setup/up; revision; switch+version (DB); ai_request = R0; readyz; log errors; greps in ai-worker container
Tag: live-<date>-b10 (local, not pushed)
Retry: budget 1; requests used n; (a) request fields; (b) consensus fields; (c) doc-4 after; (d) log line or none
KI-009 live check: two reading_state reads with times
Hard-zero (if ready): unverified AI fields n/n; auto links 0?; Matched 0?
Verdicts: KI-008 / KI-009 / KI-006
Switch at end (DB): value, version, time
Records: D117, KNOWN_ISSUES, PROJECT_STATUS
Not checked:
Deviations / open issues:
```
