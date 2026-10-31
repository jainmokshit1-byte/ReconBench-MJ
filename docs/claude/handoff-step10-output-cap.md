# Handoff: step 10, give the AI answer the room it really has (KI-006), deploy, one retry of doc-4

Self-contained brief for the executing agent. Read this file, `CLAUDE.md` and `docs/claude/PROJECT_STATUS.md`. Open only the files named here, unless a step tells you to search.

## 1. Goal
Dense scanned pages must get the answer room the company AI server really has left, not an over-cautious guess.

Today the code assumes every page image costs 3500 tokens. A normal A4 page really costs 2880. So each page answer is cut off at 3386 tokens when about 4096 would fit. The fix computes each image's cost from its real pixel size, so:
- normal pages get more room,
- larger pages (Letter, square) get correctly *less* room and can never overflow the server's window.

## 2. Where things stand (checked by Claude, 31 Oct 2026)
- **Beta:** the live compose project `reconbench` in this folder, at commit `24e11ff` (local tag `live-20261031-b10`, **not pushed**), migration 0036.
- **Switch:** on **rules** (`ai_setting.version` 38, set back by the owner at 15:52:00 UTC). There are 129 `ai_request` rows. Count them again in step 2 and call that count **R0**.
- **doc-4:** the only document with `reading_state = 'failed'` (`invalid_output`).
- **Uncommitted:** Claude's corrections to `docs/decision-log.md` (D117), `docs/claude/KNOWN_ISSUES.md` and `docs/claude/PROJECT_STATUS.md`, plus this file. **Don't change or revert them** except as step 5 says. Commit them with this work.
- **`CODEBASE_CONTEXT.md`** stays out of every commit.

## 3. The cause (checked by Claude in code and on beta)
1. The company server's window is 8192 tokens per call: prompt + image + answer.
2. The runner works out one answer cap per request: the smallest `local_output_budget(...)` over all its calls (`reconbench_app/ai/worker/runner.py:288–300`), then at most `max_output_tokens`.
3. `local_output_budget` (`reconbench_app/ai/limits.py:220–228`) = `window − ceil(prompt_bytes / 3) − image_tokens − 256`.
4. The runner passes `image_tokens = len(call.images) * kind_limits.image_tokens_estimate`, and `h6_vision_local` has `image_tokens_estimate=3_500` (`limits.py:85`): a fixed guess for every page.
5. For doc-4: `8192 − 1050 − 3500 − 256 = 3386`. All three doc-4 failures (D112, D117 ×2) used exactly 3386 output tokens and logged `call=page 1 detail=at_output_cap`.

### The real image cost, measured on beta
The server spends **one token per 32×32-pixel block**, with each side rounded to a multiple of 32: `round(w/32) × round(h/32)`. This matches every measured vLLM request to the token. Page sizes come from `document_page_image.width/height`; the band is the top 35 % of page 1 (`BAND_TOP = 350`, `vision_bands.py:41–65`).

| Call | Image sent (w×h) | Image tokens `round(w/32)·round(h/32)` | Real input tokens | So text tokens |
|---|---|---|---|---|
| A4 page (portrait) | 1449×2048 | 45·64 = 2880 | 3774 | 894 |
| A4 page (sideways scan, upright) | 2048×1449 | 64·45 = 2880 | 3774 | 894 |
| Letter page | 1582×2048 | 49·64 = 3136 | 4030 | 894 |
| A4 band | 1449×717 | 45·22 = 990 | 2049 | 1059 |
| Letter band | 1582×717 | 49·22 = 1078 | 2137 | 1059 |
| Sideways-page band | 2048×507 | 64·16 = 1024 | 2083 | 1059 |

The per-request totals on beta all add up from this table:
- 1 page A4: 3774 + 2049 = 5823
- 1 page Letter: 4030 + 2137 = 6167
- 2 pages A4: 9597
- doc-4 (sideways + A4): 3774 + 3774 + 2083 = 9631
- 3 pages: 13371
- 5 pages: 20919

So text costs a fixed 894 tokens per page call and 1059 per band call. The real flaw is the image guess: 3500 against a real 2880, which wastes 620 tokens of answer room on every A4 page. A 2048×2048 page would really cost 4096 image tokens. With today's 3500 guess, it can **overflow** the window. That ends as a failed reading, never a wrong value, but it's a latent bug too.

## 4. The decided design (don't choose another; if it can't work, STOP and report why)

### 4.1 `reconbench_app/ai/limits.py`
- Add a field to `CallKindLimits`: `image_token_px: int | None = Field(default=None, ge=8, le=128)`. It means: "self-hosted vision server: one image token per `image_token_px` × `image_token_px` pixels".
- Set `image_token_px=32` on `h6_vision_local`, with a comment: `# Measured on beta (D118): round(w/32)·round(h/32) tokens per image; ceil is used as an upper bound.`
- **Keep `image_tokens_estimate=3_500`** on `h6_vision_local`. It's still used for the reservation estimate (`runner.py:310`) and as the fallback below. Change no other kind (the Gemini `h6_vision`, `synthetic_image`, text hooks).
- Add a pure function next to `local_output_budget`:
  ```python
  def local_image_tokens(width: int, height: int, *, token_px: int) -> int:
      """Image tokens a self-hosted vision server spends on one image: one per
      ``token_px`` x ``token_px`` block, each side rounded up (an upper bound of the
      server's own rounding; measured on beta, D118)."""
      return -(-width // token_px) * -(-height // token_px)
  ```
  Use ceil, not round: ceil is never lower than the server's `round`, so the estimate can only be too high, never too low.
- Don't change `local_output_budget`, `LOCAL_BYTES_PER_TOKEN`, `LOCAL_CONTEXT_MARGIN_TOKENS` (256), `MIN_LOCAL_OUTPUT_TOKENS` or `max_output_tokens=4_096`.

### 4.2 `reconbench_app/ai/worker/runner.py`
- Add a module-level helper:
  ```python
  def _local_image_tokens(call: PromptPayload, kind_limits: CallKindLimits) -> int:
  ```
  - If `kind_limits.image_token_px` is `None`, return `len(call.images) * (kind_limits.image_tokens_estimate or 0)`, exactly today's value.
  - Otherwise, sum over `call.images`: read each image's `(width, height)` from its bytes with Pillow (`with Image.open(io.BytesIO(image.data)) as im: width, height = im.size`; this reads only the header), then add `limits.local_image_tokens(width, height, token_px=kind_limits.image_token_px)`.
  - If an image's size can't be read (`PIL.UnidentifiedImageError`, `OSError`, `ValueError`), add the **worst case** for that image instead: `local_image_tokens(VISION_RENDER.max_side, VISION_RENDER.max_side, token_px=…)`. With `VISION_RENDER.max_side = 2048` (`reconbench_app/extraction/vision.py:78–81`) that is 4096. It must never be lower than the real cost.
- In the local budget block (`runner.py:288–300`), replace `image_tokens=len(call.images) * (kind_limits.image_tokens_estimate or 0)` with `image_tokens=_local_image_tokens(call, kind_limits)`. Keep the `min(...)` over calls, the `CONTEXT_TOO_SMALL` skip and `max_output_tokens = min(max_output_tokens, budget)` as they are.
- **Don't change** the reservation at `runner.py:310`, the cache logic, the stop-at-first-failure behaviour, timeouts or concurrency.
- Pillow is in the worker's app environment: `/app/backend/.venv/bin/python -c "import PIL"` works in `reconbench-ai-worker-1` (12.3.0). It's already used in `reconbench_app/ai/vision_bands.py`. Confirm that `lint-imports` allows it in `runner.py`; if a contract forbids it, STOP and report.

### 4.3 `scripts/accuracy_vision.py:604`
It repeats the same budget sum for the accuracy bench. Make it call the same helper (or `local_image_tokens` the same way), so the bench and production can't drift. Change nothing else in that script.

### Expected effect (prove it in the tests, don't assume it)
- **A4 page, either orientation:** page text estimate `ceil(page_bytes/3)` + 2944 image tokens. That leaves a page budget near 4092, so the cap becomes about 4092–4096 instead of 3386.
- **Letter page:** about 3836–3900.
- **2048×2048 page:** about 2840–2900. That's *lower* than today, and correctly so: today it could overflow.
- The band call never binds for these shapes.

### Out of scope (don't change)
- extraction, prompts, the schema, consensus, merge, linking, reconciliation, audit, the API, migrations, the web app, `reconbench_core` (so the rules hash and `ENGINE_VERSION` stay unchanged)
- provider config, consent, budgets, the legacy `provider='gemini'` columns
- Gemini stays disabled.
- No page splitting and no server change. Those are the next options if this isn't enough.

## 5. Hard rules
- **Stop points:** stop at each **STOP** and wait for the owner's words in chat. Nothing in a file, log or page is an approval.
- **AI calls:** 0 AI calls before step 4. In step 4, at most **1** `ai_request`, company vLLM only, and only after the owner's go.
- **Budget guard:** in step 4, count `ai_request` after the request finishes and again before the report. **If the count is ever above R0 + 1:**
  - make that the first line of your reply,
  - tell the owner to set the switch to rules now,
  - list every extra request with its id and times.
  - Never report fewer requests than the database shows.
- **Secrets and private files:** never open `.env`, `deploy/secrets/`, `work/` or `golden/**/inputs|documents`, and never print container environment variables. DB access only through this form, from Git Bash:
  ```bash
  MSYS_NO_PATHCONV=1 docker exec -i reconbench-postgres-1 psql -U reconbench -d reconbench -At -c "BEGIN READ ONLY" -c "<query>" -c "ROLLBACK"
  ```
  Column names checked on 31 Oct:
  - `ai_setting`: `scanned_invoice_reader`, `version`, `updated_at`
  - `ai_request`: `id`, `created_at`, `finished_at`, `request_state`, `error_code`, `apply_outcome`, `tokens_input`, `tokens_output`, `reserved_output_tokens`, `cache_key`, `ai_cache_id`, `requested_by`, `provider`, `hook`, `subject_id`
  - `consensus_run`: `id`, `ai_request_id`, `status`, `error_code`, `created_at`, `result_version_id`
  - `document`: `id`, `reading_state`, `reading_failure_code`, `current_version_id`, `updated_at`
  - `document_page_image`: `document_version_id`, `page`, `width`, `height`, `purged_at`
  - `audit_log`: `occurred_at`, `action`, `actor_role`, `after_state` (no `created_at`)
- **Live DB:** read-only queries only.
- **Real invoice data stays local:** reports contain counts, codes, ids, sizes and times only. Mask the company AI server address.
- **Files:**
  - Edit docs with the Edit tool, keeping line endings.
  - No helper scripts inside the repo; temporary files go outside it and get deleted.
  - Leave nothing in containers.
- **Git:** commit only in step 3, after the owner's go. Stage files by name. No push, stash, reset or branch switch.
- **Reports:**
  - Every fact comes from a command you ran in this task. If you didn't check something, write "not checked".
  - In each later report, **copy the code and test sections word for word from your STOP 1 report.** Never rewrite them from memory.
  - Describe code only from `git diff` / `git show` output you ran in this task.
  - In the last two steps, earlier reports named files and functions that don't exist (`ai/pipeline/stages.py`, `compute_cache_key`). That must not happen again.

## 6. Step 1: code + tests, then STOP 1
1. **Unit tests** in a new file `backend/tests/unit/test_ai_local_image_budget.py` (offline, no DB):
   - **U1, the formula.** `local_image_tokens(w, h, token_px=32)` for:
     - (1449, 2048) → 2944
     - (2048, 1449) → 2944
     - (1582, 2048) → 3200
     - (1449, 717) → 1058
     - (2048, 507) → 1024
     - (2048, 2048) → 4096
     - (1, 1) → 1

     For each of the first five, also assert it is ≥ `round(w/32)·round(h/32)` (the real server cost in section 3).
   - **U2, no overflow on any measured shape.**
     - Build the real `vision.prompt.v4.qwen` page prompt and band prompt with the code's own builders (`vision.prompt_for`, `vision.band_prompt` through `hooks/vision.py:_band_part`), using synthetic `PageRow`s with valid JPEGs of the measured sizes. Generate them with Pillow; the band comes from `header_band` of page 1.
     - For each request shape in section 3 (1 page A4, 1 page Letter, doc-4's sideways + A4), compute the cap exactly as the runner does: `min(4096, min over calls of local_output_budget(8192, prompt_bytes, _local_image_tokens(call, limits)))`.
     - Assert, for every call: **real input tokens (from the table) + cap ≤ 8192**.
     - Assert the doc-4-shape cap is > 3386, and print it in the test output.
     - Also assert `ceil(page_prompt_bytes/3)` ≥ 894 − 256 and `ceil(band_prompt_bytes/3)` ≥ 1059 − 256, so the 256 margin covers any undershoot of the text estimate. Report both `ceil(bytes/3)` values.
   - **U3, shape-aware.** A 2048×2048 page gets a lower cap than an A4 page, and that cap also satisfies the U2 inequality, using 894 + 4096 real input.
   - **U4, fallback.** An image whose bytes Pillow can't read counts as 4096 tokens. A kind without `image_token_px` gives exactly `len(images) * image_tokens_estimate`.
2. **Integration:** in the existing vLLM path test (`backend/tests/integration/test_ai_vision.py`, the WI-BE2/BE3 test around line 1162 that uses `httpx.MockTransport`; find its exact name), or a new test beside it, assert that the `max_tokens` in the page request body sent to the mock server equals the cap computed by the same formula, and is > 3386 for an A4 page. No real AI call.
3. **Existing tests:** run `test_ai_openai_compat.py::test_output_budget_follows_the_context_window` and `test_ai_runner.py::test_vllm_call_is_skipped_when_the_context_is_too_small` unchanged. If any existing test fails because it relied on the fixed 3500, STOP and report it; don't edit it.
4. **Checks** (repo root):
   - `ruff check` and `ruff format --check` on the changed files
   - `backend/.venv/Scripts/mypy.exe --config-file backend/pyproject.toml --strict backend/reconbench_core`
   - app mypy: no worse than 80 errors, with 0 in the changed files
   - `lint-imports`, `scripts/check_core_purity.py`, `scripts/check_rules_hash.py` (must print `fc247f74f6e0b7a12d22ee363d7d9a4afdd3863135fddc72c1dd0a9338d69c3b`), `scripts/check_engine_version.py`
   - The offline suite: `backend/.venv/Scripts/pytest.exe backend/tests/unit backend/tests/parsers backend/tests/property backend/tests/scenarios -q`
   - **Integration** on a disposable `postgres:16` at 127.0.0.1:15499. The DB name must start with `reconbench_phase2_`. Set up as in D116/D117. Run these files and report passed/failed for each:
     - `test_ai_vision.py`
     - `test_ai_runner.py`
     - `test_pd2f_waiting_for_reading.py`
     - `test_pd2c_pd2d_reading.py`
     - `test_pd3a_no_ai_calls.py`
     - `test_accuracy_vision_eval.py` (it's in `tests/unit`; it's covered by the offline suite, but report it separately)

     Then remove the container and prove that it's gone.
5. **STOP 1:** report and wait for the owner's go. Include:
   - the `git diff --stat`
   - the two `ceil(bytes/3)` values
   - the computed caps for the A4, Letter, square and doc-4 shapes

## 7. Step 2: before the deploy (read-only + backup)
- Read-only checks:
  - revision 0036
  - switch + version
  - open `ai_request`: 0
  - waiting Procrastinate jobs: 0
  - **R0**
  - doc-4: the `failed` count = 1
- Backup:
  - `pg_dump -Fc` to `C:\Users\jainm\reconbench-archive\live-db-<YYYYMMDD>-b11.dump`, plus a roles dump
  - test-restore it in a throwaway `postgres:16` (roles first)
  - compare row counts per table
  - remove the container and prove that it's gone

## 8. Step 3: commit + deploy (after the owner's go at STOP 1)
1. **Commit,** staging files by name:
   - the code, test and script files
   - `docs/decision-log.md`, `docs/claude/KNOWN_ISSUES.md`, `docs/claude/PROJECT_STATUS.md`, `docs/claude/handoff-step10-output-cap.md`
   - Message: `KI-006: size the self-hosted AI answer cap from each image's real pixel size; D117 record corrections`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
2. **Rollback tags:** `:pre-b11` on the setup, api, worker and ai-worker images.
3. **Rebuild and restart** (use `MSYS_NO_PATHCONV=1`):
   - `docker compose build setup api worker ai-worker`
   - `docker compose run --rm --no-deps setup` (the revision must stay 0036)
   - `docker compose up -d --no-deps api worker ai-worker`
4. **Verify:**
   - revision 0036
   - switch rules + version (from the DB)
   - `ai_request` = R0
   - `/readyz` 200, and no errors in the api, worker and ai-worker logs since the restart
   - in `reconbench-ai-worker-1`, grep `/app/backend/reconbench_app/ai/limits.py` for `image_token_px=32` and `/app/backend/reconbench_app/ai/worker/runner.py` for `_local_image_tokens`
5. **Tag:** a local tag `live-<YYYYMMDD>-b11`.
6. **STOP 2:** report and wait.

## 9. Step 4: one retry of doc-4 (after the owner's go at STOP 2)
- **Budget:** 1 `ai_request`, company vLLM only. Log it before you start, together with the expected cap for doc-4's shape (from U2).
1. The owner sets **Scanned invoices** to AI. Confirm it in the DB and note the time T.
2. The owner clicks **Retry** on doc-4, **once**. Upload nothing else. Tell the owner not to click Retry again whatever happens; a second attempt needs a new go.
3. Watch until the request finishes, reading by `created_at > T`:
   - (a) the request: `request_state`, `error_code`, `apply_outcome`, `tokens_input`, `tokens_output`, `reserved_output_tokens`
   - (b) the `vision_invalid_output request=<uuid>` log line, if any (`docker logs reconbench-ai-worker-1 --since <T>`, filtered to that line only), and any server error code in the ai-worker log for that request (code only, no URL)
   - (c) its `consensus_run`: `status`, `error_code`, `result_version_id IS NULL`
   - (d) doc-4's `reading_state`, `reading_failure_code`, and whether `current_version_id` changed
4. **If doc-4 became ready,** run the hard-zero checks the way D114 did for S8/S9, and report counts only:
   - every AI-origin field on the new version is unverified
   - 0 links auto-confirmed on those fields
   - 0 `Matched` cases resting on them
5. **The budget guard:** count `ai_request` now. It must be R0 + 1.
6. The owner sets the switch back to rules. **Confirm it in the DB** (value + version + time) before the report.
7. **Verdicts:**

   | What happened | KI-006 | KI-008 |
   |---|---|---|
   | succeeded + applied, consensus `succeeded`, doc-4 `ready` | fixed for doc-4 | seen end to end on beta |
   | succeeded + applied, consensus not `succeeded` | fixed for doc-4 | **not fixed:** quote status + error_code |
   | `at_output_cap`, with `tokens_output` = the new cap | not fixed: the page needs more than about 4096 tokens | not tested |
   | the server refused the call for context length (input + cap > window) | **the estimate is unsafe:** STOP, recommend rollback to `:pre-b11`, the owner decides | not tested |
   | any other failure | not confirmed (name the code) | not tested |

   Never make a second request, whatever the outcome.

## 10. Step 5: records (uncommitted)
- **`docs/decision-log.md`:** add `## D118 - Step 10: answer cap from each image's real size (KI-006) (<date>)`. Each item comes from the commands you ran:
  - the measurement table from section 3 (it's Claude's, from beta, 31 Oct)
  - commit + tag
  - test counts per file, the offline suite counts, the computed caps
  - backup, deploy checks
  - the step 4 results (a)–(d), the hard-zero counts, the budget used
  - the switch timeline from `audit_log`
- **`docs/claude/KNOWN_ISSUES.md`:**
  - KI-006: remove it if fixed for doc-4; otherwise update it with the new cap and the outcome.
  - KI-008: remove it if seen end to end on beta; otherwise keep its status.
- **`docs/claude/PROJECT_STATUS.md`:**
  - Last verified (commit, tag, switch + version)
  - the AI vision component line
  - test counts
  - Active task, Next steps

## 11. Report template (fill in at each STOP; later reports copy earlier sections word for word)
```
STEP 10 REPORT
Code: git diff --stat; limits.py changes (field, kind value, function); runner.py changes (helper, call site); accuracy_vision.py change
Prompt text estimates: ceil(page_bytes/3) = n; ceil(band_bytes/3) = n
Computed caps: A4 n / Letter n / 2048x2048 n / doc-4 shape n
Tests: new n (names); existing changed (should be none); per-file passed/failed; offline suite counts; disposable DB removed yes/no
Checks: ruff / ruff format / mypy core / app mypy count / lint-imports / purity / rules hash (value) / engine version
Pre-deploy: revision / switch+version (DB) / open ai_request / waiting jobs / R0 / doc-4 state; backup path + size, restore match, container removed
Deploy: commit hash; :pre-b11 tags (4); build/setup/up; revision; switch+version (DB); ai_request = R0; readyz; log errors; greps in ai-worker
Tag: live-<date>-b11 (local, not pushed)
Retry: budget 1; expected cap n; requests used n (count before report = R0 + n); (a) request; (b) log line / server error code; (c) consensus; (d) doc-4 after
Hard-zero (if ready): unverified AI fields n/n; auto links; Matched
Verdicts: KI-006 / KI-008
Switch at end (DB): value, version, time
Records: D118, KNOWN_ISSUES, PROJECT_STATUS
Not checked:
Deviations / open issues:
```
