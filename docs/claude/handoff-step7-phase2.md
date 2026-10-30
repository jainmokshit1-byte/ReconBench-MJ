# Step 7, phase 2: four step 6 leftovers + PD-5b regression (code only, no deploy)

Brief for an executing agent. It is self-contained: open only the files named here (use grep to find a function before reading around it). Don't re-read `docs/accuracy-plan.md` §15 or the whole decision log.

## Goal
Build and test four small changes, then run the full regression (PD-5b). Nothing is deployed and beta's database and containers are not touched. Phase 3 (later, separate brief) deploys this with the owner's go.

## Hard rules (stop and ask the owner if any would be broken)
1. **Never open, list, copy or quote anything in `work/`, `.env` or `deploy/secrets/`.**
2. **No AI calls of any kind** (no Gemini, no company server). Tests use the existing fakes; never set `RB_AI_VLLM_ENDPOINTS`, `BENCH_GEMINI_KEY` or any provider key in your environment.
3. **Beta stays unchanged:** don't touch the `reconbench` compose project (no SQL, no restart, no rebuild, no `docker compose` in this folder without `-p`). Integration tests run only on a disposable PostgreSQL container on port 15499 (below); never point any `RB_*` URL at beta's database.
4. **Beta serves `web/` straight from this folder, so web edits show on beta at once.** Every web change must render nothing until the backend that feeds it is deployed: new fields are optional in the code and absent today.
5. **No migration.** If a change seems to need a new Alembic revision, STOP and report.
6. **No commits, pushes, branch switches, installs or dependency changes.** No refactoring or formatting of untouched code (`ruff format` only on new files; on edited files check that the diff shows only your hunks).
7. Preserve existing extraction, linking, reconciliation and audit behaviour. The hard-zero rule holds for every change: no false Matched, no false auto-links, no auto-accepted wrong values.
8. Counts only in reports and docs; no real invoice data anywhere.
9. Windows host, Git Bash: use `backend/.venv/Scripts/*.exe`; prefix docker commands with `MSYS_NO_PATHCONV=1`; heredocs break on an odd number of apostrophes, so write scripts to files. Node is not on PATH: prefix web commands with `export PATH="/c/Users/jainm/AppData/Local/nvm/v24.19.0:$PATH"`.

## Starting state (verified 30 Oct 2026)
- Branch `phase2-hosted-ci`, HEAD `31e08d0`; engine `0.1.13` (`backend/reconbench_core/version.py`); beta at revision 0036, switch on rules (version 28). Uncommitted Phase 1 files exist (`scripts/pd5c_*`, `backend/tests/unit/test_pd5c_score.py`, docs edits): leave them alone.
- Offline suite after step 6: 1496 passed. Web: vitest 70 passed. App mypy baseline: 80 errors (same as HEAD); core `mypy --strict` clean.

## Task A: KI-005, an empty OCR value is labelled a disagreement
- **Where:** `backend/reconbench_core/extraction/merge.py:_merge_field` (about line 224). Today, when the OCR value exists but is empty (for example an empty reference list, text `""`), and the AI read a value, it returns `CrossCheck(key, "disagree", ai_text, ocr_text, LLM_DISAGREEMENT)`.
- **Change:** after the `_held_by_person(ocr)` check (which stays first) and the `ai is None` branch (unchanged), treat an OCR value whose text is empty (`None`, `""` or an empty list, check what `_text(_plain(...))` returns) like a missing one: return the AI value with `CrossCheck(key, "ai_only", ai_text, None)`. Nothing else changes; the AI value stays unverified as before.
- **Tests:** add cases to `backend/tests/unit/test_pd2e_merge.py`: empty OCR list + AI value is `ai_only` with no `LLM_DISAGREEMENT`; non-empty different OCR value is still `disagree`; a person-held empty value is still `person_kept`.
- **Rules hash:** `scripts/check_rules_hash.py` hashes every `.py` file under `backend/reconbench_core`, so refresh `backend/reconbench_core/RULES_HASH` the way that script documents (read its `main`). **Engine version:** `scripts/check_engine_version.py` only requires a bump for its `RULE_PATHS` (align, mapping, normalize, recon, schema, seed_data); `extraction/merge.py` isn't one. Don't bump unless that script fails; report what it said.
- **Docs:** `docs/claude/KNOWN_ISSUES.md` KI-005: mark "fixed in code (D113), not deployed"; don't remove it.

## Task B: per-field origin in the Excel export (rest of PD-4c, D7)
- **Where:** `backend/reconbench_app/export_service.py:ExportService.build_export` (line 67). Today the "Invoice Recon" sheet ends with a "Reading Method" column (`READING_METHOD_LABELS`, about line 482). Field origin per value is in `field_evidence.origin` (run `\d`-equivalent via the metadata: `backend/reconbench_app/db/metadata.py`, `_tables["field_evidence"]`).
- **Change:** add one new sheet **"Field Origin"** after the existing sheets, with one row per exported invoice and header field that the Invoice Recon sheet shows: the invoice's row key exactly as Invoice Recon shows it, the field name, its origin (Rules / AI / Person / Import, from the existing labels if any) and verified Yes/No. Use the same document version the Invoice Recon row used. **Existing sheets, columns and formulas stay byte-for-byte as they are** (parity, MT-12). Apply the same text-safety (formula-injection) treatment the other sheets use. If you can't tell which version a row used, STOP and report.
- **Tests:** `backend/tests/unit/test_export_workbook.py`: existing tests unchanged and passing; add one test for the new sheet (origins of a rules field, an unverified AI field, a person-corrected field; text safety).

## Task C: "Waiting / Failed" chip in the documents list
- **Backend:** `GET /api/v1/documents` is `backend/reconbench_app/api/ingestion.py:get_documents` (line 846). Add optional `reading_state` and `reading_failure_code` to each listed document (from `document.reading_state` / `reading_failure_code`, owned by `backend/reconbench_app/reading_state.py`). No change to filtering or ordering.
- **API files:** regenerate with, from the repo root: `backend/.venv/Scripts/python.exe scripts/export_reconciliation_schemas.py`, `backend/.venv/Scripts/python.exe scripts/export_openapi.py`, then (Node PATH prefix) `npm run api:generate --prefix web`. Commit nothing; the diff of `backend/openapi.json`, `web/src/api/generated.ts` and `docs/schemas` must show only the two new fields.
- **Web:** find the component that renders this list (grep `web/src` for the generated client call of the documents list). Show a small chip only for `waiting` ("Waiting for reading") and `failed` ("Reading failed", with the plain reason from `web/src/components/readingFailure.ts`); nothing for `ready` or when the field is absent. Reuse the styles/wording of `web/src/components/ReadingStatus.tsx`; don't add actions to the list.
- **Tests:** a vitest case for waiting / failed / ready / absent; one backend test that the list returns the two fields (extend the existing test of `get_documents`; find it with grep).

## Task D: handwritten date or amount, "Use as…" (owner-approved rule, D103 decision 4)
- **Rule (approved by the owner):**
  1. A handwritten date or amount stays a note by default; nothing is applied automatically or auto-verified.
  2. A handwritten **date** can be used as the **invoice date** (`doc_date`); a handwritten **amount** as the **invoice total** (`grand_total`). No other targets.
  3. Using it is a person's action through the existing "Use suggested" path: audited, origin person, counted in mapping only after the click; refused while a reading is pending (`reading_pending`), as corrections already are.
  4. If the target field already has a different value, the web asks the person to confirm the replacement first.
  5. A note whose text doesn't parse as a date / amount stays a note only (no button).
- **Where:** `backend/reconbench_app/ingestion.py`, the vision suggestions view (about lines 5599–5710): handwritten notes become suggestions only for `_HANDWRITTEN_REFERENCE_FIELDS` (line 6006: `po_number`, `service_po_number` → `po_refs`; `grn_number` → `grn_refs`), marked `"handwritten": True`. Note types come from `backend/reconbench_core/extraction/vision.py` (`ANNOTATION_LOOKS`: includes `date` and `amount`). Extend the app side only: notes with `looks_like` `date` → suggestion for `doc_date` (kind date), `amount` → `grand_total` (kind money), parsed with the same parser the vision path already uses for `date` / `money` fields (find it in `reconbench_core/extraction/vision.py`; don't write a new parser); unparsable → skipped. Make sure these suggestions are excluded from any auto-verify (`auto_verify_field_types`) and from the AI value merge. **Don't edit `reconbench_core`** for this task.
- **Web:** `web/src/components/AiSuggestionsPanel.tsx` (the "Use suggested" button, about line 107): for a handwritten suggestion on `doc_date` / `grand_total`, label it "Use as invoice date" / "Use as invoice total"; when `current_value` is set and `matches_current` is false, confirm before applying. Renders nothing new until the backend sends such suggestions.
- **Before coding,** read the apply endpoint the button calls and confirm in your report that it records origin person + an audit row and refuses while a reading is pending. If it doesn't, STOP and report.
- **Tests:** extend the existing test that covers `"handwritten": True` suggestions (grep `backend/tests` for `handwritten`): a date note and an amount note give the two suggestions; an unparsable one gives none; applying one is audited, origin person; refused while waiting; links/mapping unchanged until applied. One vitest case for the labels and the confirm.

## PD-5b: regression (after A–D)
Run in this order; report each with its counts.
1. Static checks: `ruff check` on changed files; `backend/.venv/Scripts/mypy.exe --strict backend/reconbench_core` clean; app mypy at most the 80-error baseline (no new errors in touched files); `lint-imports` (contracts in `backend/pyproject.toml`); `scripts/check_core_purity.py`; `scripts/check_rules_hash.py`; `scripts/check_engine_version.py` (base `31e08d0`).
2. Offline suite, in the background (about 8 minutes): `backend/.venv/Scripts/pytest.exe backend/tests/unit backend/tests/parsers backend/tests/property backend/tests/scenarios -q`. Includes `tests/unit/test_export_workbook.py`, `tests/scenarios/test_provider_isolation.py`, `tests/scenarios/test_hard_zero_guards.py`. Expected ≥ 1496 + your new tests, 0 failed.
3. Golden: `backend/.venv/Scripts/python.exe scripts/golden_run.py --self-check` (rules-path results identical).
4. Integration on a disposable database (from the repo root):
   - `MSYS_NO_PATHCONV=1 docker run -d --rm --name rb-test-pg -e POSTGRES_DB=reconbench_phase2_ci -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=phase2-ephemeral-ci-only -p 127.0.0.1:15499:5432 postgres:16.15-bookworm`
   - Environment: `RB_ENV=ci`; `RB_MIGRATOR_DATABASE_URL`, `RB_DATABASE_URL`, `RB_PHASE2_TEST_DATABASE_URL` = `postgresql+psycopg://postgres:phase2-ephemeral-ci-only@127.0.0.1:15499/reconbench_phase2_ci`; the four `RB_BOOTSTRAP_*` values from `.github/workflows/ci.yml` (about line 263).
   - Setup: `alembic -c backend/alembic.ini upgrade head`; `python scripts/seed_database.py`; in `backend/`: `procrastinate --app reconbench_app.db.procrastinate_schema.app schema --apply`.
   - **First**, alone on the fresh database: `tests/integration/test_phase4_task_411_413.py` (needs no other sessions; never run before).
   - Then one file per pytest call: `test_ai_vision.py`, `test_pd2f_waiting_for_reading.py`, `test_pd2c_pd2d_reading.py`, `test_pd3a_no_ai_calls.py`, the file with the `get_documents` test, any export integration test (grep `build_export` in `backend/tests/integration`), and the background-jobs test for PD-0b-1 (grep `PD-0b-1` in `backend/tests`).
   - A test that stores AI-origin values must clean them up (fixture `forget_ai_values` in `test_ai_vision.py`). A simulated provider failure uses HTTP 500, not 503 (KI-004). `test_infrastructure_ready` needs MinIO: skip and say so.
   - Three integration failures already fail on HEAD (listed in `docs/claude/KNOWN_ISSUES.md`): report them as pre-existing only if they're on that list.
   - Afterwards stop the container and show it's gone.
5. Web: in `web/` with the Node PATH prefix: `npx tsc -b`, `npm run lint`, `npx vitest run` (expected 70 + your new cases, 0 failed).

## Records (counts only)
- **D113** in `docs/decision-log.md` after D112: what changed (A–D), the D rule as approved, the engine/rules-hash outcome, every check and test result with counts, "not deployed; phase 3 deploys with the owner's go".
- `docs/claude/PROJECT_STATUS.md`: only the Tests lines, Active task and Next steps. `docs/accuracy-plan.md`: only the PD-4c and PD-5b status sentences. KI-005 as in task A.

## Acceptance (phase 2 is done when all hold)
- A–D implemented as specified, each with its tests passing; existing export sheets unchanged.
- All PD-5b checks green, or each failure shown to be pre-existing on the KNOWN_ISSUES list.
- 0 AI calls; beta untouched (state it: no command touched the `reconbench` project); no migration; web changes inert on beta today.
- `git status --short` shows only the intended files.

## Stop and report (don't work around)
A needed migration; a needed `reconbench_core` change outside task A; the apply path in D doesn't record origin person / audit or isn't refused while pending; you can't tell which version an export row used; a new failure you can't explain; anything that would touch beta.

## Report back (paste this filled in, nothing else)
```
PHASE 2 REPORT
A KI-005: files changed; tests added (n); rules hash refreshed yes/no; check_engine_version result; engine version now
B export: files changed; new sheet columns; existing sheets unchanged yes/no (how checked); tests added (n)
C list chip: backend fields added; generated files diff = only the two fields yes/no; web component changed; tests added (backend n, vitest n)
D handwritten: apply endpoint checks (origin person / audit / pending refusal) with file:function; files changed; tests added (backend n, vitest n)
PD-5b: static checks (each: pass/fail + counts); offline suite (passed/failed/skipped, time); golden; integration (per file: passed/failed/skipped; pre-existing failures named by KI); web (tsc, lint, vitest counts)
Beta untouched: yes/no; AI calls: 0; migration: none
Records: D113 yes/no; PROJECT_STATUS / accuracy-plan / KNOWN_ISSUES lines changed
Deviations from this brief / open issues: …
Files changed (git status --short): …
```
