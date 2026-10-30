# Project status
Last verified: 2026-10-30, commit fbf6fed live as live-20261030-b7 (D114), switch on rules; Step 7 Phase 3 deploy + PD-5a verified on beta (D114); CODEBASE_CONTEXT.md untracked

## Components
Status: working-verified | implemented-untested | partial | stub | broken | not-found | unknown
Unit-level "working-verified" = offline suite passed this session; not proven on real invoices.
- Ingestion (upload, sniff, ZIP, dedupe by hash) — working-verified on beta (PD-5a S1–S4, D114) — reconbench_app/ingestion.py, tests/integration/test_ingestion.py
- Digital vs scanned PDF routing — working-verified (unit + beta S1/S2, D114) — local_hybrid.py:is_scanned; tests/parsers/test_phase4_pdf.py, test_scan_images.py
- Rules extraction (PDF/XML/CSV/Tally) — working-verified (unit + beta, D114); accuracy target not met — tests/parsers/*; docs/accuracy-plan.md §0
- AI vision reading (Gemini / vLLM) — working-verified on beta (PD-5a D114, optional AI block: 1 request to vLLM, rules + AI versions, switch returned to rules; KI-005 not exercised) — reconbench_app/ai/worker/, ai/consensus.py, reconbench_core/extraction/merge.py; tests/unit/test_pd2e_merge.py, tests/integration/test_ai_vision.py
- Waiting for reading (PD-2f, D109; live D110, D114) — working-verified (beta S5 + optional AI block, D114) — reconbench_app/reading_state.py, migration 0036; tests/integration/test_pd2f_waiting_for_reading.py
- Scanned-invoice switch + 0 LLM calls outside AI reading (PD-2c/PD-2d, D107, D114) — working-verified (beta S1–S11 0 calls on rules, optional AI block 1 call, D114) — ai/vision.py, ai/settings_service.py, migration 0035; tests/integration/test_pd2c_pd2d_reading.py
- Validation (arithmetic, GSTIN, confidence) — working-verified (unit) — reconbench_core/extraction/verify.py; tests/property
- Vendor + link mapping — working-verified (unit + beta S8, D114: 0 auto links on unverified AI fields) — tests/unit/test_phase4_mapping_links.py, test_phase4_vendor_mapping.py, test_q2_typed_links.py
- Line alignment — working-verified (unit) — tests/unit/test_phase4_alignment.py
- Reconciliation engine — working-verified (unit + beta S9, D114: 0 matched cases on unverified AI fields) — tests/unit/test_phase4_reconciliation.py, tests/scenarios/test_hard_zero_guards.py
- Ledger, review, export — working-verified on beta (S10, D114: 6 sheets incl. Field Origin with 80 rows) — ledger_service.py, api/review.py, export_service.py; tests/unit/test_export_workbook.py
- Web app — vitest 73 passed, lint + typecheck OK (2026-10-30, D114, Node v24.19.0 from nvm, not on PATH) — web/

## Tests
- `backend/.venv/Scripts/pytest.exe backend/tests/{unit,parsers,property,scenarios} -q` — 1502 passed, 22 skipped, 361 s, 2026-10-30 (D113)
- `pytest backend/tests/integration` — run on disposable postgres:16.15 at 127.0.0.1:15499: test_phase4_task_411_413.py (3 passed), test_ai_vision.py (15 passed), test_pd2f_waiting_for_reading.py (11 passed), test_pd2c_pd2d_reading.py (9 passed), test_pd3a_no_ai_calls.py (3 passed), test_ingestion.py (4 passed), test_pd_ledger_scopes.py (3 passed); 3 failures on HEAD (KNOWN_ISSUES: test_ai_h1, test_page_split); test_infrastructure_ready needs MinIO/S3 (D113)
- Web: `npx tsc -b`, `npm run lint`, `npx vitest run` (73 passed, D114); Playwright e2e not run
- `make accuracy-bench` — not run: needs Docker worker image and owner's local truth set

## Active task
- Step 7 Phase 3 complete (D114): Task 0 duplicate suggestion rows fixed; deployed to beta (tag live-20261030-b7); PD-5a click-through S1–S11 verified on beta with hard zeros intact (0 auto links, 0 false matched); optional AI block on beta (rules + AI versions, waiting state; KI-005 not exercised, stays open); live switch returned to rules (version 30).
- Hand-off ready for owner review and decision on live switch from D112 / D114.

## Next steps (max 5, highest value first)
1. Owner decides live switch from D112 / D114 results (currently on rules, version 30).
2. Owner pushes fbf6fed and tag live-20261030-b7 (plus docs commit).
3. Decide on KI-001 (internal server address in tracked files).
4. Address pre-existing test failures (KI-003: test_ai_h1, test_page_split).
5. KI-005 closes after the next AI reading on beta shows `ai_only` for an empty rules field.
