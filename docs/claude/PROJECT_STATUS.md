# Project status
Last verified: 2026-10-29, HEAD 7d11ea2 + uncommitted step 6 (D109); live is still live-20261029-b5 (D108); CODEBASE_CONTEXT.md untracked

## Components
Status: working-verified | implemented-untested | partial | stub | broken | not-found | unknown
Unit-level "working-verified" = offline suite passed this session; not proven on real invoices.
- Ingestion (upload, sniff, ZIP, dedupe by hash) — implemented-untested here (DB integration tests skipped) — reconbench_app/ingestion.py, tests/integration/test_ingestion.py
- Digital vs scanned PDF routing — working-verified (unit) — local_hybrid.py:is_scanned; tests/parsers/test_phase4_pdf.py, test_scan_images.py
- Rules extraction (PDF/XML/CSV/Tally) — working-verified (unit); accuracy target not met — tests/parsers/*; docs/accuracy-plan.md §0
- AI vision reading (Gemini / vLLM) — implemented; with the switch on AI the reading becomes the current version (PD-2e, D109, uncommitted); live calls not run — reconbench_app/ai/worker/, ai/consensus.py, reconbench_core/extraction/merge.py; tests/unit/test_pd2e_merge.py, tests/integration/test_ai_vision.py
- Waiting for reading (PD-2f, D109, uncommitted) — working-verified (integration, disposable DB) — reconbench_app/reading_state.py, migration 0036; tests/integration/test_pd2f_waiting_for_reading.py
- Scanned-invoice switch + 0 LLM calls outside AI reading (PD-2c/PD-2d, D107) — working-verified (unit + integration on disposable DB); live 29 Oct (D108) — ai/vision.py, ai/settings_service.py, migration 0035; tests/integration/test_pd2c_pd2d_reading.py
- Validation (arithmetic, GSTIN, confidence) — working-verified (unit) — reconbench_core/extraction/verify.py; tests/property
- Vendor + link mapping — working-verified (unit); D109: no auto link while an AI value is unverified, PD-2h self-support guard — tests/unit/test_phase4_mapping_links.py, test_phase4_vendor_mapping.py, test_q2_typed_links.py
- Line alignment — working-verified (unit) — tests/unit/test_phase4_alignment.py
- Reconciliation engine — working-verified (unit) — tests/unit/test_phase4_reconciliation.py, tests/scenarios/test_hard_zero_guards.py
- Ledger, review, export — implemented-untested here — ledger_service.py, api/review.py, export_service.py; tests/unit/test_export_workbook.py
- Web app — vitest 70 passed, lint + typecheck OK (2026-10-29, D109, Node v24.19.0 from nvm, not on PATH) — web/

## Tests
- `backend/.venv/Scripts/pytest.exe backend/tests/{unit,parsers,property,scenarios} -q` — 1495 passed, 1 failed (test_authorization: fixed and that file re-run, 5 passed), 22 skipped, 460 s, 2026-10-29 (with D109 changes)
- `pytest backend/tests/integration` — affected groups run on disposable postgres:16.15 at 127.0.0.1:15499 (D109, run from the repo root: some files need it for alembic.ini); a test that stores AI-origin values must remove them (fixture `forget_ai_values`) or the migration rewind tests fail; 3 failures also fail on HEAD (KNOWN_ISSUES); test_infrastructure_ready needs MinIO/S3; test_phase4_task_411_413 needs a database with no other sessions
- Web: `npx tsc -b`, `npm run lint`, `npx vitest run` (70 passed, D109) with PATH=/c/Users/jainm/AppData/Local/nvm/v24.19.0; Playwright e2e not run
- `make accuracy-bench` — not run: needs Docker worker image and owner's local truth set

## Active task
- Step 6 (PD-2e + PD-2f + PD-2h + PD-3b) built and tested, not committed or deployed (D109); plan and review in docs/claude/plan-step6.md
- Owner check of step 5 on live still pending (D108)

## Next steps (max 5, highest value first)
1. Owner review of the step 6 diff, then commit; deploy per docs/claude/handoff-step6.md "Deploy" (migration 0036, owner go).
2. Before setting the switch to AI on live: one budgeted company-vLLM reading of a synthetic scan (owner go), and a dry run on a throwaway copy of live.
3. Open points from D109: a "Waiting / failed" chip in the documents list; handwritten dates/amounts stay notes; `test_phase4_task_411_413` not run.
4. Step 7: PD-5 (5a, 5b, 5c) + per-field origin in the export (D7).
5. Decide on KI-001 (internal server address in tracked files).
