# Handoff: step 6 (PD-2e + PD-2f + PD-2h, with PD-3b)

Written 29 Oct 2026 after step 5 went live. Read this instead of exploring the repo; open only the files named here.

## Where things stand
- Step 5 (PD-2c + PD-2d) is live: commits 41b5546 (code) and c12e991 (docs), tag live-20261029-b5, D107 + D108. Live revision 0035. The switch is on "read with rules"; AI settings version 22; consent unchanged.
- Specs for step 6: `docs/accuracy-plan.md` §15.2 cards PD-2e, PD-2f, PD-2h, PD-3b (search `#### PD-2e`, etc.). Rules: D103 (`docs/decision-log.md`, search `## D103`). Don't re-read §15 in full.

## What step 5 built (reuse it)
- **Switch:** `ai_setting.scanned_invoice_reader` (`rules`/`ai`).
  - Runtime check: `SettingsSnapshot.reads_scanned_invoices` (`ai/runtime_checks.py`). It needs: switch on `ai`, active provider vllm, card enabled, `supports_vision` and `supports_json_schema` both true.
  - Settings check: `_scanned_ai_allowed` (`ai/settings_service.py`). The settings update resets the switch to rules when AI is taken away.
- **Who gets read:** `ai/vision.py:vision_eligibility`. Invoice only (`READ_DOC_TYPES`), PDF, and `facts.scanned` (any page with `document_text.scanned` true).
  - "Scanned" is the per-page scan check, never `extraction_method = pdf_ocr`: an OCR assist on a digital page is `pdf_ocr` but not scanned.
  - The rule is checked in `AiHookEnqueuer.vision_targets` and again in `ai/worker/hooks/vision.py:prepare`.
- **Method per version:** `document_version.reading_method` (`rules`/`ai`). New versions get it from `ingestion.py:_reading_method`. A correction and a consensus version copy it, because they copy the whole row.
  - The DB check `document_version_reading_method_ck` requires `ai_first`/`ai_fallback` ⇒ `ai`, parser methods ⇒ `rules`, `manual` ⇒ either. **PD-2e must insert `reading_method='ai'` with `extraction_method='ai_first'`, or the insert fails.**
- **H1:** never queued (`ingestion.py:_queue_vision_reading`). The code is kept, and `HOOKS_BY_MODE` is unchanged, so the consent fingerprint didn't change.
- **UI and export:** `web/src/components/ScannedInvoicesSwitch.tsx` (Settings). A "Read by rules / Read by AI" chip in `IngestionPreview.tsx`. A "Reading Method" last column in `export_service.py`.

## Step 6 entry points (not built yet)
- **AI answer handling:** `ai/worker/hooks/vision.py` `apply()` (~L148). Today it stores suggestions only.
  - PD-2e turns a validated answer into a new current version (`extraction_method=ai_first`, origin AI via PD-2a) through a new pure `merge_readings` with an `arb.llm` policy (OCR cross-check, `LLM_DISAGREEMENT`).
  - That code doesn't exist yet. A pure merge would sit in `reconbench_core`, which needs an `ENGINE_VERSION` bump and a rules-hash refresh.
- **P3 bug (fixed in PD-2e):** `consensus_enqueue_failed TypeError` is logged at `ai/worker/hooks/vision.py:193`.
- **Copying a version as a template:** `ai/consensus.py:_append_verified_version` (~L802).
- **PD-2f:** add a "Waiting for reading" state, plus Retry / "Read with rules" actions (audited). Mapping and reconciliation must skip such documents.
- **PD-2h:** extend `tests/unit/test_q2_typed_links.py`.
- **PD-3b:** a hypothesis test in `backend/tests/property/`.

## Open decisions to raise with the owner at the start
1. In `ai_assisted` mode, `vision_eligibility` still skips scanned invoices that don't need verification (`NOT_NEEDED`). Should "read with AI" read every scanned invoice?
2. The provider name on the chip ("AI reading <provider>") needs the provider stored per version, or derived from field provenance.
3. Gemini as a reader stays blocked until the owner re-confirms O-25.

## How to test (proven commands, Windows)
- **Offline:** `backend/.venv/Scripts/pytest.exe backend/tests/unit backend/tests/parsers backend/tests/property backend/tests/scenarios -q`. 1460 passed after step 5; it takes about 5 minutes, so run it in the background.
- **Web:** Node isn't on PATH. Prefix `export PATH="/c/Users/jainm/AppData/Local/nvm/v24.19.0:$PATH"`, then in `web/` run `npx tsc -b`, `npm run lint` and `npx vitest run`. 64 passed after step 5.
- **Integration:** use a throwaway DB only, never live.
  - Start it: `docker run -d --rm --name rb-test-pg -e POSTGRES_DB=reconbench_phase2_ci -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=phase2-ephemeral-ci-only -p 127.0.0.1:15499:5432 postgres:16.15-bookworm`.
  - Set `RB_ENV=ci`, and point `RB_MIGRATOR_DATABASE_URL`, `RB_DATABASE_URL` and `RB_PHASE2_TEST_DATABASE_URL` at `postgresql+psycopg://postgres:phase2-ephemeral-ci-only@127.0.0.1:15499/reconbench_phase2_ci`.
  - Set the four `RB_BOOTSTRAP_*` values from `.github/workflows/ci.yml` (~L263).
  - Then run `alembic -c backend/alembic.ini upgrade head`, `python scripts/seed_database.py` and `procrastinate --app reconbench_app.db.procrastinate_schema.app schema --apply`.
  - Run one test file per pytest call.
- **Migration check:** `alembic upgrade 0035`, then `python scripts/check_migration_safety.py` and `scripts/check_schema_drift.py`. Bump the CI previous-schema step in `.github/workflows/ci.yml` (~L283) and the head asserts in the `test_*_migration.py` tests and `test_phase4_task_411_413.py`. Add the new revision's DROPs to the `_REWIND` lists in `test_ai_providers_migration.py`, `test_ai_provider_connections_migration.py` and `test_ai_provider_key_move_migration.py`.
- **Checks:** run the `recon-check` skill (ruff, mypy strict, import contracts, purity, rules hash, engine version). Baselines: ruff errors only in untouched files (`test_trace_readonly.py`, `scripts/accuracy_*`, …), app mypy 80 errors.

## Gotchas found in step 5
- **Git Bash path rewriting:** it rewrites `/app/...` and `/tmp/...` in docker commands. Use `MSYS_NO_PATHCONV=1`. Windows Python needs `;` in PYTHONPATH.
- **Append-only tables:** `document_version` and `field_evidence` are append-only (trigger `*_append_only`). A migration backfill must pause the trigger for that one statement, like 0024 and 0035 do.
- **Vision mechanics tests:** `tests/integration/test_ai_vision.py` tests the reader's mechanics with a test-only `_reader_mechanics_gate` fixture that opens the step 5 gate. The real gate is tested without any patch in `test_pd2c_pd2d_reading.py`. Its `_company_server()` helper sets up a capable vLLM card.
- **Real scanned PDFs:** `tests/support/scan_images.py:scanned_invoice_pdf()` gives a real scanned PDF (local Tesseract OCR works).
- **Known failures that also fail on HEAD** (KI-003): `test_ai_h1.py::test_ai_first_suggests_for_a_clean_pdf…` and two `test_page_split.py` tests. Scenario reruns on the same DB can fail with `PROVIDER_BACKOFF` (KI-004), so use a fresh DB.
- **PD-1b guard:** a settings update can't change the vLLM model while it is the page reader (`provider_capability_missing`).

## Deploy (when step 6 is ready, with the owner's go)
- **Live stack:** `compose.yaml` on this PC. The web dev server serves `web/` straight from the folder, so web edits show on live immediately; keep them inert until the backend ships.
- **Before:** read-only checks via `docker exec reconbench-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" …'`: revision, queued `ai_request`, waiting `procrastinate_jobs`.
- **Backup:** `pg_dump -Fc` into `C:\Users\jainm\reconbench-archive\live-db-<date>-b6.dump`. Test-restore it in a throwaway container and compare per-table row counts.
- **Rollout:** `docker tag reconbench-{setup,api,worker,ai-worker}:latest …:pre-b6`, then `docker compose build setup api worker ai-worker`, then `docker compose run --rm --no-deps setup` (migration), then `docker compose up -d --no-deps api worker ai-worker`.
- **After:** check the revision, the AI settings version and fingerprint, the `ai_request` count, and the logs. Record a new D entry; local tag `live-<date>-b6`. The owner pushes.
- **Never** open `work/`, `.env` or `deploy/secrets/`. Mask the company AI server address in any report.
