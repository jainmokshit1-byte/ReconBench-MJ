# Handoff: step 7 (PD-5a + PD-5b + PD-5c, with the step 6 leftovers)

Written 30 Oct 2026 after step 6 went live and passed its dry run. Read this instead of exploring the repo; open only the files named here.

## Where things stand
- Step 6 (PD-2e, PD-2f, PD-2h, PD-3b) is live: commit 493bc9e, tag `live-20261030-b6`, migration 0036, engine 0.1.13. Entries: D109 (built), D110 (live), D111 (dry run). Pushed to `origin/phase2-hosted-ci` with the tag.
- The live switch "Scanned invoices" is on **read with rules** (settings version 24). It stays on rules until PD-5c has measured both methods (D103, §15.0 principle 9). It was found on AI once (29 Oct, D110); check it at the start of every session.
- Live after D111: revision 0036, 113 `ai_request` rows, 0 values with origin `ai`, all 545 documents `ready`.
- Specs: `docs/accuracy-plan.md` §15.2 cards **PD-5a, PD-5b, PD-5c** (search `#### PD-5a`). Rules: D103 (`docs/decision-log.md`, search `## D103`). Step 6 design: `docs/claude/plan-step6.md`. Don't re-read §15 in full.

## Step 7 scope
1. **PD-5a, end to end on a throwaway copy of live:** every scenario of the post-demo audit's §8, by clicking. Acceptance: hard zeros 0; 0 `ai_request` for rules-read documents and from mapping/reconciliation; requests only to the active provider.
   - **Blocker:** the audit is `work/postdemo-audit-20261026.md`, and `work/` must never be opened. Ask the owner for the §8 scenario list (pasted in chat), or propose one built from §15.0 and the PD cards for the owner to approve.
2. **PD-5b, regression:** offline suite, integration suites, rules-path golden/bench hash identical (`make golden`), export parity (`tests/unit/test_export_workbook.py`), secret-leak and provider isolation (`tests/scenarios/test_provider_isolation.py`, `test_hard_zero_guards.py`), background jobs (PD-0b-1). Also run `tests/integration/test_phase4_task_411_413.py`, never run in step 6 (needs a database with no other sessions).
3. **PD-5c, comparative measurement:** the same unique real scanned invoices through both switch positions in the app; per-field counts with n (D83); no accuracy claim before this. The live switch decision comes from these numbers.
   - Real invoices are in `work/` (never open, copy or quote them). The owner must provide them as uploads on a throwaway copy, or run `make accuracy-bench` themselves (owner only). Agree the method with the owner first.
   - Budget: the company vLLM budget is set by Claude, with **one owner go + a log line before any call** (memory `feedback_vllm_budget`). Gemini only after the owner re-confirms O-25.
4. **Step 6 leftovers** (each small; confirm scope with the owner):
   - **KI-005:** an empty OCR reference list is labelled a disagreement. Fix in `reconbench_core/extraction/merge.py` (`_merge_field`: treat an empty OCR value as missing → `ai_only`); add a case to `backend/tests/unit/test_pd2e_merge.py`. Core change ⇒ engine 0.1.13 → 0.1.14, rules hash refresh, redeploy.
   - **Per-field origin in the Excel export** (D7, rest of PD-4c): `backend/reconbench_app/export_service.py`; parity test MT-12.
   - **"Waiting / failed" chip in the documents list** (only the document page shows it today).
   - **Handwritten dates/amounts:** shown as notes only. The owner may want a "Use as…" choice (pick the target field, counts after the click). This is a D103 decision-4 gap, so ask first.

## Open decisions to raise with the owner at the start
1. PD-5a scenario list (the audit's §8 is in `work/`).
2. PD-5c method: uploads of owner-chosen scans on a throwaway copy, or the owner's local bench; how many documents; the vLLM call budget.
3. Which step 6 leftovers go into step 7.
4. Gemini as reader: still blocked until O-25 is re-confirmed.

## How step 6 works (reuse it, don't rebuild)
- **States:** `document.reading_state` is `ready` / `waiting` / `failed`, with `reading_failure_code`. Owned by `backend/reconbench_app/reading_state.py`.
  - Parse marks a scanned invoice `waiting` when the switch is on AI (`ingestion.py:_queue_vision_reading` → `ai/enqueue.py:scanned_readings`).
  - Every final AI request state fails the document through `reading_state.fail_finished_readings`, called from `ai/ledger.py`. Stalled readings are swept by `fail_stalled_readings`.
- **Merge:** pure `reconbench_core/extraction/merge.py:merge_readings` (policy `arb.llm`). Cross-check entries go to `extras.cross_check`; OCR header values go to `extras.ocr_reading`.
- **AI version:** appended by `IngestionService.append_ai_version`, called from `ai/consensus.py:ConsensusService._persist` in the general worker. The document then becomes `ready` in the same transaction.
- **Skipping:** mapping, ledger and reconciliation load through `MappingService._load_current`, which filters on `ready`. Vendor resolution has the same filter.
- **Person actions:** `POST /documents/{id}/reading/retry` and `/reading/use-rules` (audited). A correction is refused (`reading_pending`) while a reading is pending.
- **Link guards:** in `reconbench_core/mapping/links.py` — `_has_unverified_ai_values` (no auto link) and `_self_supported_total` (PD-2h).
- **Web:** `web/src/components/ReadingStatus.tsx` and `readingFailure.ts` (plain reasons); the provider label is in `IngestionPreview.tsx`.

## How to test (proven, Windows)
- **Offline:** `backend/.venv/Scripts/pytest.exe backend/tests/unit backend/tests/parsers backend/tests/property backend/tests/scenarios -q`. 1496 passed after step 6 (about 8 min, run in the background).
- **Web:** prefix `export PATH="/c/Users/jainm/AppData/Local/nvm/v24.19.0:$PATH"`, then in `web/` run `npx tsc -b`, `npm run lint`, `npx vitest run` (70 passed).
- **Integration:** use a disposable database only, and **run pytest from the repo root** (some files need it for `alembic.ini`).
  - Container: `docker run -d --rm --name rb-test-pg -e POSTGRES_DB=reconbench_phase2_ci -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=phase2-ephemeral-ci-only -p 127.0.0.1:15499:5432 postgres:16.15-bookworm`.
  - Environment: `RB_ENV=ci`; `RB_MIGRATOR_DATABASE_URL`, `RB_DATABASE_URL` and `RB_PHASE2_TEST_DATABASE_URL` all set to `postgresql+psycopg://postgres:phase2-ephemeral-ci-only@127.0.0.1:15499/reconbench_phase2_ci`; the four `RB_BOOTSTRAP_*` values from `.github/workflows/ci.yml` (~L263).
  - Setup: `alembic -c backend/alembic.ini upgrade head`, then `python scripts/seed_database.py`, then (in `backend/`) `procrastinate --app reconbench_app.db.procrastinate_schema.app schema --apply`. Run one test file per pytest call.
- **Migration check:** start a **separate fresh container** (roles are cluster-wide, so a second database in the same cluster fails at revision 0014). Upgrade to the previous revision, run `scripts/check_migration_safety.py`, then upgrade head and run `scripts/check_schema_drift.py`. A new revision also needs the CI previous-schema step (`.github/workflows/ci.yml` ~L283), the head asserts in the `test_*_migration.py` files and `test_phase4_task_411_413.py`, and the `_REWIND` lists.
- **Checks:** the `recon-check` skill. Baselines: app mypy 80 errors (same as HEAD), core `mypy --strict` clean, ruff errors only in untouched files.

## Gotchas found in steps 6 and 7 prep
- **AI-origin data in the shared test DB:** a test that stores values with origin `ai` must delete them afterwards (fixture `forget_ai_values` in `tests/integration/test_ai_vision.py`), or the migration rewind tests fail on the next run.
- **Provider backoff:** a simulated 503 puts the company server into a shared 30 s backoff (KI-004). Use HTTP 500 for a final failure in tests.
- **Authorization registry:** every public `IngestionService` method needs an authorization declaration (`@requires(...)`; worker methods use `"files.upload"`), or `tests/unit/test_authorization.py` fails.
- **Formatting:** `ruff format` on a whole file reformats untouched code (some files are not formatted at HEAD). Format only new files, or check that the diff shows only your hunks.
- **Shell:** the Bash tool's heredocs break on an odd number of apostrophes; write edit scripts to the scratchpad and run them. Use `MSYS_NO_PATHCONV=1` for docker commands. Windows `python` inside the app containers is not the app environment; grep files under `/app/backend` instead.
- **Live web:** live serves `web/` straight from this folder, so web edits show on live at once. Keep them inert until the backend ships.

## Throwaway copy of live (method used in D111)
- Compose project `-p rbcopy` with an override that gives every published port a different host port (api 28000, postgres 25432, minio 29000/29001) and new volumes.
- Restore a fresh `pg_dump -Fc` of live after creating the roles in the new cluster, run the copy's setup service (migrations, bucket), then start api, worker and ai-worker.
- Drive the service layer from inside the copy containers. Never type the owner's password.
- Afterwards: set the copy's switch back to rules, then `down -v`, and prove that no `rbcopy` container, volume or network is left and live is unchanged.

## Deploy (when step 7 changes code, with the owner's go)
- **Before:** read-only checks (revision, switch, open `ai_request`, waiting jobs).
- **Backup:** `pg_dump -Fc` into `C:\Users\jainm\reconbench-archive\live-db-<date>-b7.dump`, test-restore it with the roles created first, and compare per-table row counts.
- **Rollout:** `:pre-b7` tags on setup, api, worker and ai-worker; `docker compose build setup api worker ai-worker`; `docker compose run --rm --no-deps setup`; `docker compose up -d --no-deps api worker ai-worker`.
- **After:** check revision, switch, settings version and fingerprint, the `ai_request` count, logs and `/readyz`, and grep the running containers for the new code. Record a D entry and a local tag `live-<date>-b7`. The owner pushes.
- **Never** open `work/`, `.env` or `deploy/secrets/`. Mask the company AI server address in every report.
